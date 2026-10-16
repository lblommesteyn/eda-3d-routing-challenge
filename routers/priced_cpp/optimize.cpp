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
            mode < 0 || mode > 14 || (mode & 3) == 3 || (warm != 0 && warm != 1))
            throw std::runtime_error("invalid optimizer parameters");
        const int accurate = mode & 3;
        const bool neighbor_polish = mode & 4;
        const bool has_dual = mode & 8;
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
            if (iteration%1000 == 0 && iteration) {
                std::fill(owner.begin(),owner.end(),-1);
                std::fill(blocked.begin(),blocked.end(),false);
                for (int n = 0; n < nnet; ++n) { nets[n].route = best[n]; paint(nets[n].route,n); }
                total = best_delay;
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
            std::set<int> blockers;
            if (warm) for (int v : desired.vertices) {
                int n = previous_owner[v];
                if (n >= 0 && n != target) blockers.insert(n);
            }
            for (const auto& route : old) for (int v : route.vertices) previous_owner[v] = -1;
            std::vector<std::pair<int,Route>> trial;
            paint(desired,target); trial.emplace_back(target,std::move(desired));
            long long after = trial.front().second.tree.delay;
            if (warm) for (int n : group) if (n != target && !blockers.count(n)) {
                paint(nets[n].route,n); after += nets[n].route.tree.delay;
                trial.emplace_back(n,nets[n].route);
            }
            std::shuffle(members.begin(),members.end(),rng);
            bool complete = true;
            for (int n : members) if (n != target && (!warm || blockers.count(n))) {
                if (elapsed() >= seconds) { complete = false; break; }
                Candidate candidate;
                bool routed = query(n,accurate ? compact : zero,blocked,rng(),candidate);
                if (!routed) { complete = false; break; }
                Route route = make_route(std::move(candidate),nets[n].pins);
                after += route.tree.delay;
                paint(route,n); trial.emplace_back(n,std::move(route));
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
            double heat = temperature * (1.-static_cast<double>(iteration%1000)/999.);
            long long delta = after-before;
            failed_repairs += !complete;
            uphill_proposals += complete && delta > 0;
            double draw = std::generate_canonical<double,53>(rng);
            bool commit = complete && (delta <= 0 || (heat > 0 && draw < std::exp(-delta/heat)));
            if (commit) {
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
