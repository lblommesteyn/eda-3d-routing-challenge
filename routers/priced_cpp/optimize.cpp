// Native neighborhood search reuses the same per-net search implementation.
#define PRICED_ROUTER_LIBRARY
#include "router.cpp"
#include <chrono>
#include <random>
#include <array>

struct Route {
    Candidate tree;
    std::vector<int> vertices;
};
struct NetState {
    int id;
    std::vector<int> pins;
    Route route;
    long long lower = 0;
    std::vector<std::pair<int,double>> bids;
    double priority = 0;
};

Route make_route(Candidate tree, const std::vector<int>& pins) {
    std::set<int> vertices(pins.begin(), pins.end());
    for (auto [a,b] : tree.edges) { vertices.insert(a); vertices.insert(b); }
    return {std::move(tree), std::vector<int>(vertices.begin(), vertices.end())};
}

int main() {
    try {
        std::ios::sync_with_stdio(false);
        Grid g;
        std::cin >> g.w >> g.h >> g.layers >> g.via;
        if (g.w <= 0 || g.h <= 0 || g.layers <= 0 || g.via <= 0 ||
            1LL*g.w*g.h*g.layers > std::numeric_limits<int>::max())
            throw std::runtime_error("invalid optimizer grid");
        g.layer_delay.resize(g.layers);
        for (int& value : g.layer_delay) std::cin >> value;
        g.pin_owner.assign(g.size(), -1);
        int count;
        std::cin >> count;
        for (int i = 0; i < count; ++i) {
            int v, owner; std::cin >> v >> owner; g.pin_owner.at(v) = owner;
        }
        int nnet, iterations, max_group, mode, warm;
        double seconds, temperature;
        std::uint64_t seed;
        std::cin >> nnet >> iterations >> seconds >> temperature >> seed >> max_group >> mode >> warm;
        if (nnet <= 0 || iterations <= 0 || !std::isfinite(seconds) || seconds <= 0 ||
            !std::isfinite(temperature) || temperature < 0 || max_group < 2 ||
            mode < 0 || mode > 16382 || (mode & 3) == 3 || (warm != 0 && warm != 1))
            throw std::runtime_error("invalid optimizer parameters");
        const int accurate = mode & 3;
        const bool neighbor_polish = mode & 4;
        const bool has_dual = mode & 8;
        // Negotiated group repair (mode bit 16): a share of moves re-route the whole
        // ripped-up group jointly with a short PathFinder loop instead of one net at a time.
        int neg_pct = 0, neg_iters = 0;
        double neg_present = 0, neg_growth = 1, neg_history = 0;
        if (mode & 16) {
            std::cin >> neg_pct >> neg_iters >> neg_present >> neg_growth >> neg_history;
            if (neg_pct < 0 || neg_pct > 100 || neg_iters < 1 || !std::isfinite(neg_present) || neg_present < 0 ||
                !std::isfinite(neg_growth) || neg_growth < 1 || !std::isfinite(neg_history) || neg_history < 0)
                throw std::runtime_error("invalid negotiation parameters");
        }
        // Annealing cycle length (mode bit 32): moves per heat/cool cycle, after which
        // the working state restarts from the best. Default 1000.
        int cycle = 1000;
        if (mode & 32) {
            std::cin >> cycle;
            // 0 = one anneal over the whole time budget: heat falls linearly with
            // elapsed time and the working state never restarts from the best.
            if (cycle < 0 || cycle == 1) throw std::runtime_error("invalid cycle length");
        }
        // Search margin (mode bit 64): see Grid::margin.
        if (mode & 64) {
            std::cin >> g.margin;
            if (g.margin < 0) throw std::runtime_error("invalid search margin");
        }
        // Fanout-first repairs (mode bit 128): on this percent of moves the ripped-up
        // nets are re-planned largest pin count first instead of in random order, so
        // high-fanout nets (whose detours cost once per sink) get first pick of space.
        int order_pct = 0;
        if (mode & 128) {
            std::cin >> order_pct;
            if (order_pct < 0 || order_pct > 100) throw std::runtime_error("invalid fanout order percent");
        }
        // Ideal-tree moves (mode bit 256): on this percent of moves the target takes its
        // shortest tree through the other nets (softly priced), every net on that tree is
        // ripped up (at most big_cap nets), and the displaced nets are negotiated around it.
        // Unlike the regional moves this can move a whole high-fanout net at once.
        int big_pct = 0, big_cap = 0;
        if (mode & 256) {
            std::cin >> big_pct >> big_cap;
            if (big_pct < 0 || big_pct > 100 || big_cap < 2) throw std::runtime_error("invalid ideal-tree move");
            if (!(mode & 16)) throw std::runtime_error("ideal-tree moves need negotiation settings");
        }
        // Region kicks (mode bit 512): every cycle restart from the best state first
        // rips up a whole region of about kick_nets nets and renegotiates it from scratch
        // (kick_rounds rounds, present kick_present growing by kick_growth), accepted
        // whatever its cost. The cycle then anneals from that new basin; the best state
        // is kept, so a kick that leads nowhere is undone by the next restart.
        int kick_nets = 0, kick_rounds = 0;
        double kick_present = 0, kick_growth = 0;
        if (mode & 512) {
            std::cin >> kick_nets >> kick_rounds >> kick_present >> kick_growth;
            if (kick_nets < 2 || kick_rounds < 1 || !(kick_present > 0) || !(kick_growth >= 1))
                throw std::runtime_error("invalid region kick");
            if (!(mode & 16)) throw std::runtime_error("region kicks need negotiation settings");
        }
        // From-scratch construction (mode bit 1024): the input
        // routes are empty, and every net is routed by one joint negotiation
        // (scratch_rounds rounds, present scratch_present growing by scratch_growth,
        // history scratch_history) before annealing starts.
        // Exact negotiation (mode bit 2048): group negotiations price whole trees
        // (each vertex price paid once, build_priced_tree) instead of per-sink paths.
        // The from-scratch construction always does.
        bool neg_exact = mode & 2048;
        int scratch_rounds = 0;
        double scratch_present = 0, scratch_growth = 1, scratch_history = 0;
        if (mode & 1024) {
            std::cin >> scratch_rounds >> scratch_present >> scratch_growth >> scratch_history;
            if (scratch_rounds < 1 || !(scratch_present > 0) || !(scratch_growth >= 1) || !(scratch_history >= 0))
                throw std::runtime_error("invalid scratch construction");
        }
        // Graft (mode bit 4096): the listed nets carry routes transplanted from another
        // solution. They are painted first; every other net whose input route collides
        // with them is ripped up, and the ripped nets are renegotiated (exact trees,
        // negotiation settings) around everything else before annealing starts.
        // Fanout bias (mode bit 8192): while hot, the acceptance test weighs the delay
        // change of a net with p >= 3 pins by 1 + bias*min(1,(p-2)/4)*(heat/temperature), steering the
        // anneal toward layouts that favour multi-sink nets. The weight fades to 1 as the
        // anneal cools; the reported and best delays are always the true objective.
        double fanout_bias = 0;
        if (mode & 8192) {
            std::cin >> fanout_bias;
            if (!std::isfinite(fanout_bias) || fanout_bias < 0) throw std::runtime_error("invalid fanout bias");
        }
        std::vector<int> graft;
        if (mode & 4096) {
            int graft_count; std::cin >> graft_count;
            if (graft_count < 1 || graft_count > nnet) throw std::runtime_error("invalid graft count");
            graft.resize(graft_count);
            for (int& n : graft) { std::cin >> n; if (n < 0 || n >= nnet) throw std::runtime_error("invalid graft net"); }
            if (!(mode & 16)) throw std::runtime_error("grafting needs negotiation settings");
        }
        std::vector<double> capacity_price(g.size(),0);
        if (has_dual) {
            std::cin >> count;
            if (count < 0 || count > g.size()) throw std::runtime_error("invalid dual price count");
            for (int i = 0; i < count; ++i) {
                int v; double value; std::cin >> v >> value;
                if (!std::isfinite(value) || value < 0) throw std::runtime_error("invalid dual price");
                capacity_price.at(v) = value;
            }
        }
        std::vector<NetState> nets(nnet);
        std::vector<int> owner(g.size(), -1), reuse(g.size(), 0);
        std::vector<int> previous_owner(g.size(), -1);
        std::vector<char> blocked(g.size(), false), unblocked(g.size(), false);
        std::vector<double> zero(g.size(), 0), prices(g.size(), 0);
        std::vector<double> compact(g.size(), 1e-4/g.size());
        long long total = 0, lower = 0;
        for (int i = 0; i < nnet; ++i) {
            int npins, edges;
            Candidate tree;
            std::cin >> nets[i].id >> npins >> tree.delay >> edges;
            nets[i].pins.resize(npins);
            for (int& pin : nets[i].pins) std::cin >> pin;
            for (int j = 0; j < edges; ++j) {
                int a,b; std::cin >> a >> b; tree.edges.emplace_back(a,b);
            }
            if (has_dual) {
                int bids; std::cin >> bids;
                if (bids < 0 || bids > g.size()) throw std::runtime_error("invalid bid count");
                for (int j = 0; j < bids; ++j) {
                    int v; double value; std::cin >> v >> value;
                    if (v < 0 || v >= g.size() || !std::isfinite(value) || value < 0 || value > capacity_price[v]+1e-8)
                        throw std::runtime_error("invalid net bid");
                    nets[i].bids.emplace_back(v,value);
                }
            }
            nets[i].route = make_route(std::move(tree), nets[i].pins);
            if (!graft.empty()) {
                Candidate bound;
                if (!build_spt(g,nets[i].id,nets[i].pins,zero,unblocked,reuse,0,0,bound))
                    throw std::runtime_error("unreachable net");
                nets[i].lower = bound.delay; lower += bound.delay;
                continue;
            }
            for (int v : nets[i].route.vertices) {
                if (owner.at(v) >= 0) throw std::runtime_error("initial capacity conflict");
                owner[v] = i; blocked[v] = true;
            }
            Candidate bound;
            if (!build_spt(g,nets[i].id,nets[i].pins,zero,unblocked,reuse,0,0,bound))
                throw std::runtime_error("unreachable net");
            nets[i].lower = bound.delay;
            total += nets[i].route.tree.delay; lower += bound.delay;
        }
        if (!std::cin) throw std::runtime_error("truncated optimizer input");
        std::vector<Route> best;
        for (const auto& net : nets) best.push_back(net.route);
        long long best_delay = total;
        std::mt19937_64 rng(seed);
        auto fanout_first = [&](std::vector<int>& list) {
            if (order_pct > 0 && static_cast<int>(rng()%100) < order_pct)
                std::stable_sort(list.begin(),list.end(),[&](int a, int b) {
                    return nets[a].pins.size() > nets[b].pins.size();
                });
        };
        int accepted = 0, improvements = 0, performed = 0;
        int failed_repairs = 0, uphill_proposals = 0;
        auto start = std::chrono::steady_clock::now();
        auto elapsed = [&]() { return std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count(); };
        auto query = [&](int n, const std::vector<double>& cost, const std::vector<char>& obstacles,
                         std::uint64_t query_seed, Candidate& result) {
            if (accurate == 2)
                return build(g,nets[n].id,nets[n].pins,cost,obstacles,reuse,0,result,query_seed,true);
            if (accurate == 1)
                return build_priced_tree(g,nets[n].id,nets[n].pins,cost,obstacles,reuse,0,query_seed,result);
            return build_spt(g,nets[n].id,nets[n].pins,cost,obstacles,reuse,0,query_seed,result);
        };
        auto clear = [&](const Route& route) { for (int v : route.vertices) { owner[v] = -1; blocked[v] = false; } };
        auto paint = [&](const Route& route, int nid) {
            for (int v : route.vertices) {
                if (owner[v] >= 0) throw std::runtime_error("trial capacity conflict");
                owner[v] = nid; blocked[v] = true;
            }
        };
        std::vector<int> neg_count(g.size(), 0);
        std::vector<double> neg_hist(g.size(), 0), neg_price(g.size(), 0);
        // Routes `order` jointly on the free vertices (blocked = everything outside the
        // group). Vertex price = history + present * (group nets using it); present grows
        // each round and overused vertices gain history. Returns false unless the group
        // ends conflict-free. Leaves owner/blocked untouched.
        auto negotiate = [&](const std::vector<int>& order, std::vector<Route>& routed) {
            int m = static_cast<int>(order.size());
            routed.assign(m, Route{});
            std::vector<int> touched;
            double present = neg_present;
            auto use = [&](const Route& route, int d) {
                for (int v : route.vertices) {
                    if (d > 0) touched.push_back(v);
                    neg_count[v] += d;
                    neg_price[v] = neg_hist[v] + present * neg_count[v];
                }
            };
            auto reroute = [&](int i) {
                Candidate candidate;
                bool found = neg_exact
                    ? build_priced_tree(g,nets[order[i]].id,nets[order[i]].pins,neg_price,blocked,reuse,0,rng(),candidate)
                    : build_spt(g,nets[order[i]].id,nets[order[i]].pins,neg_price,blocked,reuse,0,rng(),candidate);
                if (!found) return false;
                routed[i] = make_route(std::move(candidate),nets[order[i]].pins);
                use(routed[i],1);
                return true;
            };
            bool ok = true;
            for (int i = 0; i < m && ok; ++i) ok = reroute(i);
            for (int round = 0; ok && round < neg_iters; ++round) {
                std::sort(touched.begin(),touched.end());
                touched.erase(std::unique(touched.begin(),touched.end()),touched.end());
                bool conflict = false;
                for (int v : touched) if (neg_count[v] > 1) { conflict = true; neg_hist[v] += neg_history; }
                if (!conflict) break;
                present *= neg_growth;
                for (int v : touched) neg_price[v] = neg_hist[v] + present * neg_count[v];
                for (int i = 0; i < m && ok; ++i) {
                    bool hit = false;
                    for (int v : routed[i].vertices) if (neg_count[v] > 1) { hit = true; break; }
                    if (!hit) continue;
                    use(routed[i],-1);
                    ok = reroute(i);
                }
            }
            if (ok) {
                std::sort(touched.begin(),touched.end());
                touched.erase(std::unique(touched.begin(),touched.end()),touched.end());
                for (int v : touched) if (neg_count[v] > 1) { ok = false; break; }
            }
            for (int v : touched) { neg_count[v] = 0; neg_hist[v] = 0; neg_price[v] = 0; }
            return ok;
        };
        long long negotiated_moves = 0, negotiated_legal = 0, negotiated_commits = 0, big_moves = 0, big_commits = 0;
        long long kicks = 0, kicks_legal = 0;
        if (scratch_rounds) {
            for (int n = 0; n < nnet; ++n) clear(nets[n].route);
            std::vector<int> order(nnet);
            for (int n = 0; n < nnet; ++n) order[n] = n;
            std::shuffle(order.begin(),order.end(),rng);
            fanout_first(order);
            int saved_iters = neg_iters;
            double saved_present = neg_present, saved_growth = neg_growth, saved_history = neg_history;
            neg_iters = scratch_rounds; neg_present = scratch_present; neg_growth = scratch_growth; neg_history = scratch_history;
            bool saved_exact = neg_exact;
            neg_exact = true;
            std::vector<Route> routed;
            bool ok = negotiate(order,routed);
            neg_exact = saved_exact;
            neg_iters = saved_iters; neg_present = saved_present; neg_growth = saved_growth; neg_history = saved_history;
            if (!ok) throw std::runtime_error("scratch negotiation did not converge");
            total = 0;
            for (int i = 0; i < nnet; ++i) {
                paint(routed[i],order[i]); total += routed[i].tree.delay;
                nets[order[i]].route = std::move(routed[i]);
            }
            best_delay = total;
            for (int i = 0; i < nnet; ++i) best[i] = nets[i].route;
            std::cerr << "scratch delay " << total << " seconds " << elapsed() << '\n';
        }
        if (!graft.empty()) {
            std::vector<char> grafted(nnet,0);
            for (int n : graft) { grafted[n] = 1; paint(nets[n].route,n); }
            std::vector<int> ripped;
            for (int n = 0; n < nnet; ++n) {
                if (grafted[n]) continue;
                bool hit = false;
                for (int v : nets[n].route.vertices) if (owner[v] >= 0) { hit = true; break; }
                if (hit) ripped.push_back(n); else paint(nets[n].route,n);
            }
            std::shuffle(ripped.begin(),ripped.end(),rng);
            fanout_first(ripped);
            bool saved_exact = neg_exact;
            neg_exact = true;
            std::vector<Route> routed;
            bool ok = ripped.empty() || negotiate(ripped,routed);
            neg_exact = saved_exact;
            if (!ok) throw std::runtime_error("graft repair did not converge");
            for (int i = 0; i < static_cast<int>(ripped.size()); ++i) {
                paint(routed[i],ripped[i]); nets[ripped[i]].route = std::move(routed[i]);
            }
            total = 0;
            for (int n = 0; n < nnet; ++n) total += nets[n].route.tree.delay;
            best_delay = total;
            for (int i = 0; i < nnet; ++i) best[i] = nets[i].route;
            std::cerr << "graft nets " << graft.size() << " ripped " << ripped.size() << " delay " << total << '\n';
        }
        auto kick = [&]() {
            ++kicks;
            std::vector<double> excess(nnet);
            for (int i = 0; i < nnet; ++i) excess[i] = 1 + std::max(0LL,nets[i].route.tree.delay-nets[i].lower);
            int seed_net = std::discrete_distribution<int>(excess.begin(),excess.end())(rng);
            const auto& on = nets[seed_net].route.vertices;
            int center = on[rng()%on.size()];
            int plane = g.w*g.h, cx = center%g.w, cy = (center%plane)/g.w;
            std::set<int> group{seed_net};
            for (int radius = 1; static_cast<int>(group.size()) < kick_nets && radius < std::max(g.w,g.h); ++radius) {
                std::vector<int> ring;
                for (int y = std::max(0,cy-radius); y <= std::min(g.h-1,cy+radius); ++y)
                    for (int x = std::max(0,cx-radius); x <= std::min(g.w-1,cx+radius); ++x)
                        for (int z = 0; z < g.layers; ++z) {
                            int n = owner[(z*g.h+y)*g.w+x];
                            if (n >= 0 && !group.count(n)) ring.push_back(n);
                        }
                std::sort(ring.begin(),ring.end());
                ring.erase(std::unique(ring.begin(),ring.end()),ring.end());
                std::shuffle(ring.begin(),ring.end(),rng);
                for (int n : ring) { if (static_cast<int>(group.size()) >= kick_nets) break; group.insert(n); }
            }
            std::vector<int> members(group.begin(),group.end());
            std::vector<Route> old;
            long long before = 0;
            for (int n : members) { old.push_back(nets[n].route); before += nets[n].route.tree.delay; clear(nets[n].route); }
            std::vector<int> order(members);
            std::shuffle(order.begin(),order.end(),rng);
            fanout_first(order);
            int saved_iters = neg_iters;
            double saved_present = neg_present, saved_growth = neg_growth;
            neg_iters = kick_rounds; neg_present = kick_present; neg_growth = kick_growth;
            std::vector<Route> routed;
            bool ok = negotiate(order,routed);
            neg_iters = saved_iters; neg_present = saved_present; neg_growth = saved_growth;
            if (!ok) {
                for (int i = 0; i < static_cast<int>(members.size()); ++i) paint(old[i],members[i]);
                return;
            }
            ++kicks_legal;
            long long after = 0;
            for (int i = 0; i < static_cast<int>(order.size()); ++i) {
                paint(routed[i],order[i]); after += routed[i].tree.delay;
                nets[order[i]].route = std::move(routed[i]);
            }
            total += after-before;
        };
        auto save_best = [&]() {
            if (total < best_delay) {
                best_delay = total; ++improvements;
                for (int i = 0; i < nnet; ++i) best[i] = nets[i].route;
            }
        };
        auto polish = [&]() {
            std::vector<int> order(nnet);
            for (int i = 0; i < nnet; ++i) order[i] = i;
            std::shuffle(order.begin(),order.end(),rng);
            for (int n : order) {
                if (elapsed() >= seconds) break;
                Route old = nets[n].route;
                clear(old);
                Candidate candidate;
                bool routed = accurate == 2 ? query(n,compact,blocked,rng(),candidate)
                    : build_spt(g,nets[n].id,nets[n].pins,zero,blocked,reuse,0,rng(),candidate);
                if (!routed)
                    throw std::runtime_error("legal incumbent became unreachable during polish");
                Route improved = make_route(std::move(candidate),nets[n].pins);
                if (improved.tree.delay <= old.tree.delay) {
                    total += improved.tree.delay-old.tree.delay;
                    nets[n].route = std::move(improved);
                }
                paint(nets[n].route,n);
                save_best();
            }
        };
        for (int iteration = 0; iteration < iterations && elapsed() < seconds && best_delay > lower; ++iteration) {
            performed = iteration+1;
            if (cycle && iteration%cycle == 0 && iteration) {
                std::fill(owner.begin(),owner.end(),-1);
                std::fill(blocked.begin(),blocked.end(),false);
                for (int n = 0; n < nnet; ++n) { nets[n].route = best[n]; paint(nets[n].route,n); }
                total = best_delay;
                if (kick_nets) kick();
            }
            if (iteration%100 == 0) polish();
            if (has_dual && iteration%100 == 0) {
                for (int n = 0; n < nnet; ++n) {
                    nets[n].priority = 0;
                    for (auto [v,bid] : nets[n].bids)
                        if (owner[v] >= 0 && owner[v] != n && bid >= capacity_price[v]-1e-8)
                            nets[n].priority += bid;
                }
            }
            std::vector<double> weights(nnet);
            for (int i = 0; i < nnet; ++i)
                weights[i] = 1 + std::max(0LL,nets[i].route.tree.delay-nets[i].lower) + nets[i].priority;
            int target = iteration%5 == 0 ? rng()%nnet : std::discrete_distribution<int>(weights.begin(),weights.end())(rng);
            if (big_pct > 0 && static_cast<int>(rng()%100) < big_pct) {
                ++big_moves;
                double s = std::array<double,4>{0,.2,.5,1}[rng()%4];
                for (int v = 0; v < g.size(); ++v) prices[v] = owner[v] >= 0 && owner[v] != target ? s : 0;
                Candidate ideal;
                if (!build_spt(g,nets[target].id,nets[target].pins,prices,unblocked,reuse,0,rng(),ideal)) continue;
                Route desired = make_route(std::move(ideal),nets[target].pins);
                std::set<int> group{target};
                for (int v : desired.vertices) if (owner[v] >= 0) group.insert(owner[v]);
                if (group.size() < 2 || static_cast<int>(group.size()) > big_cap) continue;
                std::vector<int> members(group.begin(),group.end());
                std::vector<Route> old;
                long long before = 0;
                for (int n : members) { old.push_back(nets[n].route); before += nets[n].route.tree.delay; clear(nets[n].route); }
                paint(desired,target);
                std::vector<int> order;
                for (int n : members) if (n != target) order.push_back(n);
                std::shuffle(order.begin(),order.end(),rng);
                fanout_first(order);
                std::vector<Route> routed;
                bool ok = negotiate(order,routed);
                long long after = 0;
                if (ok) {
                    for (int i = 0; i < static_cast<int>(order.size()); ++i) { paint(routed[i],order[i]); after += routed[i].tree.delay; }
                    // the soft prices may have bent the target; straighten it in what is left
                    clear(desired);
                    Candidate straight;
                    if (!build_spt(g,nets[target].id,nets[target].pins,zero,blocked,reuse,0,rng(),straight))
                        throw std::runtime_error("ideal tree became unreachable");
                    if (straight.delay < desired.tree.delay) desired = make_route(std::move(straight),nets[target].pins);
                    paint(desired,target);
                    after += desired.tree.delay;
                }
                double heat = cycle ? temperature * (1.-static_cast<double>(iteration%cycle)/(cycle-1.))
                                  : temperature * std::max(0.,1.-elapsed()/seconds);
                long long delta = after-before;
                double draw = std::generate_canonical<double,53>(rng);
                if (ok && (delta <= 0 || (heat > 0 && draw < std::exp(-delta/heat)))) {
                    ++big_commits;
                    nets[target].route = std::move(desired);
                    for (int i = 0; i < static_cast<int>(order.size()); ++i) nets[order[i]].route = std::move(routed[i]);
                    total += delta; ++accepted;
                    save_best();
                } else {
                    if (ok) for (int i = 0; i < static_cast<int>(order.size()); ++i) clear(routed[i]);
                    clear(desired);
                    for (int i = 0; i < static_cast<int>(members.size()); ++i) paint(old[i],members[i]);
                }
                continue;
            }
            double soft = std::array<double,6>{0,.05,.2,.5,1,2}[rng()%6];
            double dual_scale = has_dual ? std::array<double,5>{0,.05,.1,.25,1}[rng()%5] : 0;
            for (int v = 0; v < g.size(); ++v)
                prices[v] = (owner[v] >= 0 && owner[v] != target ? soft : 0) + (accurate ? compact[v] : 0)
                            + dual_scale*capacity_price[v];
            for (auto [v,bid] : nets[target].bids) prices[v] = std::max(0.,prices[v]-dual_scale*bid);
            Route desired;
            // Half of region moves choose a center from the incumbent. Their
            // preliminary unblocked tree would be discarded without being used.
            if (!warm || iteration%2 == 0) {
                Candidate desired_tree;
                bool found = query(target,prices,unblocked,rng(),desired_tree);
                if (!found) continue;
                desired = make_route(std::move(desired_tree),nets[target].pins);
            }
            std::set<int> group{target};
            if (!warm) for (int v : desired.vertices) if (owner[v] >= 0) group.insert(owner[v]);
            if (static_cast<int>(group.size()) > max_group) continue;
            if (warm || iteration%2 == 0) {
                const auto& centers = warm && iteration%2 ? nets[target].route.vertices : desired.vertices;
                int center;
                if (has_dual) {
                    std::vector<double> pressure;
                    for (int v : centers) pressure.push_back(1+capacity_price[v]);
                    center = centers[std::discrete_distribution<int>(pressure.begin(),pressure.end())(rng)];
                } else center = centers[rng()%centers.size()];
                int plane = g.w*g.h, cx = center%g.w, cy = (center%plane)/g.w;
                int radius = 2+rng()%11;
                std::vector<int> nearby;
                for (int y = std::max(0,cy-radius); y <= std::min(g.h-1,cy+radius); ++y)
                    for (int x = std::max(0,cx-radius); x <= std::min(g.w-1,cx+radius); ++x)
                        for (int z = 0; z < g.layers; ++z) {
                            int n = owner[(z*g.h+y)*g.w+x];
                            if (n >= 0 && !group.count(n)) nearby.push_back(n);
                        }
                std::sort(nearby.begin(),nearby.end());
                nearby.erase(std::unique(nearby.begin(),nearby.end()),nearby.end());
                std::shuffle(nearby.begin(),nearby.end(),rng);
                for (int n : nearby) { if (static_cast<int>(group.size()) >= max_group) break; group.insert(n); }
            }
            std::vector<int> members(group.begin(),group.end());
            std::vector<Route> old;
            long long before = 0;
            for (int n : members) {
                old.push_back(nets[n].route); before += nets[n].route.tree.delay;
                for (int v : nets[n].route.vertices) previous_owner[v] = n;
                clear(nets[n].route);
            }
            const bool negotiated = neg_pct > 0 && static_cast<int>(rng()%100) < neg_pct;
            std::set<int> blockers;
            std::vector<std::pair<int,Route>> trial;
            long long after = 0;
            bool complete = true;
            if (negotiated) {
                ++negotiated_moves;
                for (const auto& route : old) for (int v : route.vertices) previous_owner[v] = -1;
                std::vector<int> order(members);
                std::shuffle(order.begin(),order.end(),rng);
                fanout_first(order);
                std::vector<Route> routed;
                complete = negotiate(order,routed);
                if (complete) {
                    ++negotiated_legal;
                    for (int i = 0; i < static_cast<int>(order.size()); ++i) {
                        paint(routed[i],order[i]); after += routed[i].tree.delay;
                        trial.emplace_back(order[i],std::move(routed[i]));
                    }
                }
                for (int n : members) blockers.insert(n);
            } else {
            if (warm) {
                Candidate repaired;
                // Occasionally price occupied vertices even after releasing the
                // region. This trades a small seed detour for cheaper repairs
                // of its neighbors instead of always forcing a shortest seed.
                const auto& seed_prices = iteration%4 == 3 ? prices : zero;
                bool routed = query(target,accurate ? compact : seed_prices,blocked,rng(),repaired);
                if (!routed) throw std::runtime_error("incumbent seed became unreachable");
                desired = make_route(std::move(repaired),nets[target].pins);
            }
            if (warm) for (int v : desired.vertices) {
                int n = previous_owner[v];
                if (n >= 0 && n != target) blockers.insert(n);
            }
            for (const auto& route : old) for (int v : route.vertices) previous_owner[v] = -1;
            paint(desired,target); trial.emplace_back(target,std::move(desired));
            after = trial.front().second.tree.delay;
            if (warm) for (int n : group) if (n != target && !blockers.count(n)) {
                paint(nets[n].route,n); after += nets[n].route.tree.delay;
                trial.emplace_back(n,nets[n].route);
            }
            std::shuffle(members.begin(),members.end(),rng);
            fanout_first(members);
            for (int n : members) if (n != target && (!warm || blockers.count(n))) {
                if (elapsed() >= seconds) { complete = false; break; }
                Candidate candidate;
                bool routed = query(n,accurate ? compact : zero,blocked,rng(),candidate);
                if (!routed) { complete = false; break; }
                Route route = make_route(std::move(candidate),nets[n].pins);
                after += route.tree.delay;
                paint(route,n); trial.emplace_back(n,std::move(route));
            }
            }
            // The rebuilt routes were optimal when planned, but moving them
            // may free shorter paths for neighbors whose routes were retained.
            // Recover those benefits before judging the neighborhood's cost.
            if (neighbor_polish && warm && complete && after > before) {
                std::vector<int> polish_order;
                for (int i = 0; i < static_cast<int>(trial.size()); ++i) {
                    int n = trial[i].first;
                    if (n != target && !blockers.count(n) && trial[i].second.tree.delay > nets[n].lower)
                        polish_order.push_back(i);
                }
                std::sort(polish_order.begin(),polish_order.end(),[&](int a, int b) {
                    return trial[a].second.tree.delay-nets[trial[a].first].lower >
                           trial[b].second.tree.delay-nets[trial[b].first].lower;
                });
                if (polish_order.size() > 6) polish_order.resize(6);
                for (int i : polish_order) {
                    if (elapsed() >= seconds || after <= before) break;
                    auto& item = trial[i];
                    clear(item.second);
                    Candidate candidate;
                    if (!build_spt(g,nets[item.first].id,nets[item.first].pins,zero,blocked,reuse,0,rng(),candidate))
                        throw std::runtime_error("legal trial became unreachable during neighbor polish");
                    if (candidate.delay <= item.second.tree.delay) {
                        after += candidate.delay-item.second.tree.delay;
                        item.second = make_route(std::move(candidate),nets[item.first].pins);
                    }
                    paint(item.second,item.first);
                }
            }
            // Repeated heat/cool cycles revisit the best state; a positive floor
            // would leave the working layout unnecessarily expensive at the end.
            double heat = cycle ? temperature * (1.-static_cast<double>(iteration%cycle)/(cycle-1.))
                                  : temperature * std::max(0.,1.-elapsed()/seconds);
            long long delta = after-before;
            failed_repairs += !complete;
            uphill_proposals += complete && delta > 0;
            double weighted = static_cast<double>(delta);
            if (complete && fanout_bias > 0 && temperature > 0) {
                double extra = fanout_bias*heat/temperature;
                for (const auto& item : trial)
                    if (nets[item.first].pins.size() >= 3)
                        weighted += extra*std::min(1.,(nets[item.first].pins.size()-2)/4.)
                                    *static_cast<double>(item.second.tree.delay-nets[item.first].route.tree.delay);
            }
            double draw = std::generate_canonical<double,53>(rng);
            bool commit = complete && (weighted <= 0 || (heat > 0 && draw < std::exp(-weighted/heat)));
            if (commit) {
                negotiated_commits += negotiated;
                for (auto& item : trial) nets[item.first].route = std::move(item.second);
                total += delta; ++accepted;
                save_best();
            } else {
                for (const auto& item : trial) clear(item.second);
                // old[] follows the sorted group, not the shuffled planning order.
                int i = 0;
                for (int n : group) { paint(old[i],n); ++i; }
            }
            if (iteration%100 == 0) {
                std::cout << "P " << performed << ' ' << accepted << ' ' << best_delay << ' ' << total << ' ' << elapsed() << '\n';
                std::cout.flush();
            }
        }
        if (mode & 16)
            std::cerr << "negotiated moves " << negotiated_moves << " legal " << negotiated_legal
                      << " committed " << negotiated_commits << " ideal moves " << big_moves
                      << " committed " << big_commits << " kicks " << kicks << " legal " << kicks_legal << '\n';
        std::cout << "R " << nnet << ' ' << best_delay << ' ' << performed << ' ' << accepted << ' ' << improvements << ' ' << elapsed()
                  << ' ' << failed_repairs << ' ' << uphill_proposals << '\n';
        for (int i = 0; i < nnet; ++i) {
            std::cout << nets[i].id << ' ' << best[i].tree.delay << ' ' << best[i].tree.edges.size() << '\n';
            for (auto [a,b] : best[i].tree.edges) std::cout << a << ' ' << b << '\n';
        }
    } catch (const std::exception& error) {
        std::cerr << "optimizer: " << error.what() << '\n'; return 1;
    }
}
