// Native per-net candidate generator. JSON is handled by route.py.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <queue>
#include <set>
#include <stdexcept>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

using Edge = std::pair<int, int>;
struct Candidate {
    std::vector<Edge> edges;
    long long delay = 0;
    double price = 0;
};

struct Grid {
    int w, h, layers, via;
    // Shortest-path-tree searches stay within the pins' bounding box grown by this
    // many cells (negative = whole grid), falling back to the whole grid if that fails.
    int margin = -1;
    std::vector<int> layer_delay, pin_owner;
    mutable std::map<std::vector<int>, std::vector<long long>> heuristics;
    int size() const { return w * h * layers; }
    template<class F> void neighbors(int u, F visit) const {
        int plane = w * h, z = u / plane, y = (u % plane) / w, x = u % w;
        int cost = layer_delay[z];
        if (x) visit(u - 1, cost);
        if (x + 1 < w) visit(u + 1, cost);
        if (y) visit(u - w, cost);
        if (y + 1 < h) visit(u + w, cost);
        if (z) visit(u - plane, via);
        if (z + 1 < layers) visit(u + plane, via);
    }
    int delay(int a, int b) const {
        return std::abs(a - b) == w * h ? via : layer_delay[a / (w * h)];
    }
    const std::vector<long long>& heuristic(int, const std::vector<int>& pins) const {
        auto found = heuristics.find(pins);
        if (found != heuristics.end()) return found->second;
        std::vector<long long> result(size(), 0);
        for (int v = 0; v < size(); ++v) {
            int z = v / (w*h), x = v % w, y = (v % (w*h)) / w;
            long long best = std::numeric_limits<long long>::max();
            for (std::size_t i = 1; i < pins.size(); ++i) {
                int target = pins[i], tz = target / (w*h);
                int length = std::abs(x - target % w) + std::abs(y - (target % (w*h)) / w);
                for (int layer = 0; layer < layers; ++layer) {
                    long long cost = 1LL * length * layer_delay[layer]
                        + 1LL * (std::abs(z-layer) + std::abs(tz-layer)) * via;
                    best = std::min(best, cost);
                }
            }
            if (pins.size() > 1) result[v] = best;
        }
        return heuristics.emplace(pins, std::move(result)).first->second;
    }
};

std::uint64_t tie_rank(int vertex, std::uint64_t seed) {
    if (!seed) return static_cast<std::uint64_t>(vertex);
    std::uint64_t value = static_cast<std::uint64_t>(vertex) + seed * 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

// Zero-price SPT is exact for sum of driver-to-sink delays with fixed obstacles.
// Unioning predecessor paths preserves a tree; unused branches are discarded.
bool build_spt_within(const Grid& g, int nid, const std::vector<int>& pins,
                      const std::vector<double>& prices, const std::vector<char>& blocked,
                      const std::vector<int>& reuse, double diversity, std::uint64_t seed,
                      Candidate& result, int margin) {
    int n = g.size(), driver = pins.front(), left = 0;
    const int plane = g.w * g.h;
    int x0 = 0, x1 = g.w - 1, y0 = 0, y1 = g.h - 1;
    if (margin >= 0) {
        x0 = g.w; x1 = -1; y0 = g.h; y1 = -1;
        for (int pin : pins) {
            int x = pin % g.w, y = (pin % plane) / g.w;
            x0 = std::min(x0, x); x1 = std::max(x1, x); y0 = std::min(y0, y); y1 = std::max(y1, y);
        }
        x0 = std::max(0, x0 - margin); x1 = std::min(g.w - 1, x1 + margin);
        y0 = std::max(0, y0 - margin); y1 = std::min(g.h - 1, y1 + margin);
    }
    // Search state lives in reusable buffers; a vertex's entries are valid only when
    // its stamp equals this search's, so a search costs what it touches instead of
    // clearing whole-grid arrays (80k vertices on the 116x116 tiers).
    struct Buffers {
        std::vector<unsigned> stamp;
        std::vector<double> distance;
        std::vector<long long> true_distance;
        std::vector<int> parent;
        std::vector<char> closed, remaining, used;
        unsigned current = 0;
    };
    thread_local Buffers b;
    if (static_cast<int>(b.stamp.size()) != n) {
        b.stamp.assign(n, 0); b.distance.assign(n, 0); b.true_distance.assign(n, 0); b.parent.assign(n, -1);
        b.closed.assign(n, false); b.remaining.assign(n, false); b.used.assign(n, false); b.current = 0;
    }
    if (++b.current == 0) { std::fill(b.stamp.begin(), b.stamp.end(), 0); b.current = 1; }
    const unsigned now = b.current;
    auto touch = [&](int v) {
        if (b.stamp[v] != now) {
            b.stamp[v] = now; b.distance[v] = std::numeric_limits<double>::infinity();
            b.true_distance[v] = 0; b.parent[v] = -1; b.closed[v] = b.remaining[v] = b.used[v] = false;
        }
    };
    for (int pin : pins) {
        if (blocked[pin]) return false;
        touch(pin);
        if (pin != driver && !b.remaining[pin]) { b.remaining[pin] = true; ++left; }
    }
    auto& distance = b.distance;
    auto& true_distance = b.true_distance;
    auto& parent = b.parent;
    // Settled vertices are final. Without this, rounding in fractional prices can
    // re-open a settled vertex and swap its parent after a sink's true delay was
    // recorded, so the extracted tree and its reported delay disagree.
    auto& closed = b.closed;
    auto& remaining = b.remaining;
    auto& used = b.used;
    const auto& heuristic = g.heuristic(nid, pins);
    using Item = std::tuple<double, double, std::uint64_t, int>;
    std::priority_queue<Item, std::vector<Item>, std::greater<Item>> heap;
    distance[driver] = 0;
    heap.emplace(heuristic[driver], 0, tie_rank(driver, seed), driver);
    while (!heap.empty() && left) {
        auto item = heap.top(); heap.pop();
        double priority = std::get<0>(item);
        int u = std::get<3>(item);
        double cost = distance[u];
        if (priority != cost + heuristic[u] || std::get<1>(item) != (seed ? 0 : -cost)) continue;
        if (closed[u]) continue;
        closed[u] = true;
        if (remaining[u]) { remaining[u] = false; --left; }
        g.neighbors(u, [&](int v, int edge_delay) {
            if (blocked[v] || (g.pin_owner[v] >= 0 && g.pin_owner[v] != nid)) return;
            if (margin >= 0) {
                int x = v % g.w, y = (v % plane) / g.w;
                if (x < x0 || x > x1 || y < y0 || y > y1) return;
            }
            touch(v);
            if (closed[v]) return;
            double next = cost + edge_delay + prices[v] + diversity * reuse[v];
            if (next < distance[v]) {
                distance[v] = next;
                true_distance[v] = true_distance[u] + edge_delay;
                parent[v] = u;
                // Seeded alternatives randomize the entire equal-cost frontier,
                // including layer choices; always preferring depth fixes those ties.
                heap.emplace(next + heuristic[v], seed ? 0 : -next, tie_rank(v, seed), v);
            }
        });
    }
    if (left) return false;
    used[driver] = true;
    double price = prices[driver];
    for (std::size_t i = 1; i < pins.size(); ++i) {
        result.delay += true_distance[pins[i]];
        int v = pins[i];
        while (!used[v]) {
            used[v] = true;
            price += prices[v];
            int u = parent[v];
            if (u < 0) throw std::runtime_error("broken shortest-path tree");
            result.edges.emplace_back(std::min(u, v), std::max(u, v));
            v = u;
        }
    }
    result.price += price;
    std::sort(result.edges.begin(), result.edges.end());
    return true;
}

bool build_spt(const Grid& g, int nid, const std::vector<int>& pins,
               const std::vector<double>& prices, const std::vector<char>& blocked,
               const std::vector<int>& reuse, double diversity, std::uint64_t seed,
               Candidate& result) {
    if (g.margin >= 0 && build_spt_within(g, nid, pins, prices, blocked, reuse, diversity, seed, result, g.margin))
        return true;
    return build_spt_within(g, nid, pins, prices, blocked, reuse, diversity, seed, result, -1);
}

// Root-distance-seeded tree growth favors short driver-to-sink paths.
// It is a heuristic for multi-sink trees, not an exact Steiner-tree solver.
bool build(const Grid& g, int nid, const std::vector<int>& pins,
           const std::vector<double>& prices, const std::vector<char>& blocked,
           const std::vector<int>& reuse, double diversity, Candidate& result,
           std::uint64_t seed = 0, bool guided = false) {
    const int n = g.size();
    std::vector<char> tree(n, false), remaining(n, false);
    std::vector<long long> root_delay(n, 0);
    std::vector<int> sources{pins.front()};
    for (int pin : pins) if (blocked[pin]) return false;
    tree[pins.front()] = true;
    const auto* heuristic = guided ? &g.heuristic(nid,pins) : nullptr;
    auto potential = [&](int v) { return heuristic ? (*heuristic)[v] : 0.; };
    int left = 0;
    for (std::size_t i = 1; i < pins.size(); ++i) {
        if (pins[i] != pins.front() && !remaining[pins[i]]) {
            remaining[pins[i]] = true;
            ++left;
        }
    }
    while (left) {
        std::vector<double> distance(n, std::numeric_limits<double>::infinity());
        std::vector<int> parent(n, -1);
        using Item = std::tuple<double, std::uint64_t, int>;
        std::priority_queue<Item, std::vector<Item>, std::greater<Item>> heap;
        for (int source : sources) {
            distance[source] = static_cast<double>(root_delay[source]);
            heap.emplace(distance[source]+potential(source), tie_rank(source,seed), source);
        }
        int found = -1;
        while (!heap.empty()) {
            auto item = heap.top(); heap.pop();
            int u = std::get<2>(item);
            double cost = distance[u];
            if (std::get<0>(item) != cost+potential(u)) continue;
            if (remaining[u]) { found = u; break; }
            g.neighbors(u, [&](int v, int edge_delay) {
                if (tree[v] || blocked[v] ||
                    (g.pin_owner[v] >= 0 && g.pin_owner[v] != nid)) return;
                double next = cost + edge_delay + prices[v] + diversity * reuse[v];
                if (next < distance[v]) {
                    distance[v] = next;
                    parent[v] = u;
                    heap.emplace(next+potential(v), tie_rank(v,seed), v);
                }
            });
        }
        if (found < 0) return false;
        std::vector<int> path;
        int current = found;
        while (!tree[current]) {
            path.push_back(current);
            current = parent[current];
            if (current < 0) throw std::runtime_error("broken predecessor chain");
        }
        for (auto it = path.rbegin(); it != path.rend(); ++it) {
            int v = *it;
            result.edges.emplace_back(std::min(current, v), std::max(current, v));
            root_delay[v] = root_delay[current] + g.delay(current, v);
            tree[v] = true;
            sources.push_back(v);
            current = v;
            if (remaining[v]) { remaining[v] = false; --left; }
        }
    }
    for (std::size_t i = 1; i < pins.size(); ++i) result.delay += root_delay[pins[i]];
    for (int v : sources) result.price += prices[v];
    std::sort(result.edges.begin(), result.edges.end());
    return true;
}

// Subset DP for the actual priced objective: each vertex price is paid once,
// while a shared stem's physical delay is paid once per downstream sink.
// Small fanout keeps the exponential state space bounded. Larger nets and
// zero-price queries use SPT; the latter is already exact for the delay objective.
bool build_priced_tree(const Grid& g, int nid, const std::vector<int>& pins,
                       const std::vector<double>& prices, const std::vector<char>& blocked,
                       const std::vector<int>& reuse, double diversity, std::uint64_t seed,
                       Candidate& result) {
    int sinks = static_cast<int>(pins.size())-1, total = g.size();
    std::vector<double> effective(total);
    bool priced = false;
    for (int v = 0; v < total; ++v) {
        effective[v] = prices[v] + diversity * reuse[v];
        priced |= effective[v] != 0;
    }
    if (sinks <= 1 || sinks > 7 || !priced)
        return build_spt(g, nid, pins, prices, blocked, reuse, diversity, seed, result);
    for (int pin : pins) if (blocked[pin]) return false;
    std::vector<int> vertices;
    if (sinks <= 3) {
        vertices.resize(total);
        for (int v = 0; v < total; ++v) vertices[v] = v;
    } else {
        // For larger fanout solve exactly on a small induced corridor assembled
        // from three valid proxy trees. This preserves a feasible upper bound
        // without allocating 2^fanout states over the entire grid.
        std::set<int> corridor(pins.begin(), pins.end());
        std::vector<double> scaled = prices;
        for (double& price : scaled) price /= sinks;
        for (int mode = 0; mode < 3; ++mode) {
            Candidate proxy;
            bool found = mode == 2
                ? build(g, nid, pins, prices, blocked, reuse, diversity, proxy)
                : build_spt(g, nid, pins, mode == 1 ? scaled : prices, blocked, reuse, diversity, seed, proxy);
            if (found) for (auto [a,b] : proxy.edges) { corridor.insert(a); corridor.insert(b); }
        }
        vertices.assign(corridor.begin(), corridor.end());
    }
    int n = static_cast<int>(vertices.size());
    std::vector<int> index(total, -1), local_pins;
    for (int v = 0; v < n; ++v) index[vertices[v]] = v;
    for (int pin : pins) local_pins.push_back(index[pin]);
    auto neighbors = [&](int u, auto visit) {
        g.neighbors(vertices[u], [&](int global, int delay) {
            if (index[global] >= 0) visit(index[global], delay);
        });
    };
    int states = 1 << sinks;
    std::vector<double> cost(states*n, std::numeric_limits<double>::infinity());
    std::vector<int> parent(states*n, -1), split(states*n, 0);
    auto legal = [&](int u) { int v = vertices[u]; return !blocked[v] && (g.pin_owner[v] < 0 || g.pin_owner[v] == nid); };
    using Item = std::tuple<double, std::uint64_t, int>;
    for (int mask = 1; mask < states; ++mask) {
        int weight = 0;
        for (int bits = mask; bits; bits >>= 1) weight += bits & 1;
        int base = mask*n;
        if (weight == 1) {
            int bit = 0;
            while (!(mask & (1 << bit))) ++bit;
            cost[base+local_pins[bit+1]] = effective[pins[bit+1]];
        } else {
            for (int v = 0; v < n; ++v) if (legal(v)) {
                for (int a = (mask-1)&mask; a; a = (a-1)&mask) {
                    int b = mask ^ a;
                    if (!b || a > b) continue;
                    double combined = cost[a*n+v] + cost[b*n+v] - effective[vertices[v]];
                    if (combined < cost[base+v]) {
                        cost[base+v] = combined;
                        split[base+v] = a;
                    }
                }
            }
        }
        std::priority_queue<Item, std::vector<Item>, std::greater<Item>> heap;
        for (int v = 0; v < n; ++v) if (legal(v) && std::isfinite(cost[base+v]))
            heap.emplace(cost[base+v], tie_rank(v, seed), v);
        while (!heap.empty()) {
            auto item = heap.top(); heap.pop();
            double d = std::get<0>(item);
            int u = std::get<2>(item);
            if (d != cost[base+u]) continue;
            neighbors(u, [&](int v, int delay) {
                if (!legal(v)) return;
                double next = d + weight * delay + effective[vertices[v]];
                if (next < cost[base+v]) {
                    cost[base+v] = next;
                    parent[base+v] = u;
                    split[base+v] = 0;
                    heap.emplace(next, tie_rank(v, seed), v);
                }
            });
        }
    }
    int full = states-1, driver = pins.front();
    if (!std::isfinite(cost[full*n+local_pins.front()])) return false;
    std::set<Edge> union_edges;
    std::vector<char> collected(states*n, false);
    std::function<void(int,int)> collect = [&](int v, int mask) {
        while (!collected[mask*n+v]) {
            int state = mask*n+v;
            collected[state] = true;
            if (parent[state] >= 0) {
                int u = parent[state];
                union_edges.emplace(std::min(vertices[u],vertices[v]), std::max(vertices[u],vertices[v]));
                v = u;
            } else {
                if (split[state]) {
                    collect(v, split[state]);
                    collect(v, mask ^ split[state]);
                }
                break;
            }
        }
    };
    collect(local_pins.front(), full);
    // Overlapping DP branches can leave redundant edges. Extract a physical
    // shortest-path tree in their union, then charge only retained resources.
    std::unordered_map<int, std::vector<int>> adjacency;
    for (auto [a,b] : union_edges) { adjacency[a].push_back(b); adjacency[b].push_back(a); }
    const long long infinity = 1LL << 60;
    std::vector<long long> distance(total, infinity);
    std::vector<int> previous(total, -1);
    using Physical = std::pair<long long,int>;
    std::priority_queue<Physical, std::vector<Physical>, std::greater<Physical>> heap;
    distance[driver] = 0; heap.emplace(0,driver);
    while (!heap.empty()) {
        auto [d,u] = heap.top(); heap.pop();
        if (d != distance[u]) continue;
        for (int v : adjacency[u]) {
            long long next = d + g.delay(u,v);
            if (next < distance[v]) { distance[v] = next; previous[v] = u; heap.emplace(next,v); }
        }
    }
    std::vector<char> used(total, false);
    used[driver] = true;
    std::set<Edge> chosen;
    for (std::size_t i = 1; i < pins.size(); ++i) {
        int v = pins[i];
        if (distance[v] == infinity) return false;
        result.delay += distance[v];
        while (!used[v]) {
            used[v] = true;
            int u = previous[v];
            chosen.emplace(std::min(u,v), std::max(u,v));
            v = u;
        }
    }
    for (int v = 0; v < total; ++v) if (used[v]) result.price += prices[v];
    result.edges.assign(chosen.begin(), chosen.end());
    return true;
}

#ifndef PRICED_ROUTER_LIBRARY
int main() {
    try {
        std::ios::sync_with_stdio(false);
        Grid g;
        if (!(std::cin >> g.w >> g.h >> g.layers >> g.via)) return 1;
        if (g.w <= 0 || g.h <= 0 || g.layers <= 0 || g.via <= 0 ||
            1LL * g.w * g.h * g.layers > std::numeric_limits<int>::max())
            throw std::runtime_error("invalid grid");
        g.layer_delay.resize(g.layers);
        for (int& cost : g.layer_delay) std::cin >> cost;
        g.pin_owner.assign(g.size(), -1);
        int count;
        std::cin >> count;
        for (int i = 0; i < count; ++i) {
            int v, owner; std::cin >> v >> owner;
            g.pin_owner.at(v) = owner;
        }
        int jobs; std::cin >> jobs;
        std::cout.precision(17);
        // -1 selects a persistent worker: read jobs until the input closes.
        for (int job = 0; jobs < 0 || job < jobs; ++job) {
            int nid, npins, k, attempts, strategy; double diversity;
            std::uint64_t seed;
            if (!(std::cin >> nid >> npins >> k >> attempts >> diversity >> strategy >> seed)) {
                if (jobs < 0 && std::cin.eof()) break;
                throw std::runtime_error("truncated request");
            }
            if (npins <= 0 || k <= 0 || attempts <= 0 || diversity < 0)
                throw std::runtime_error("invalid request");
            std::vector<int> pins(npins);
            for (int& pin : pins) { std::cin >> pin; g.pin_owner.at(pin); }
            std::vector<double> prices(g.size(), 0);
            std::vector<char> blocked(g.size(), false);
            std::cin >> count;
            for (int i = 0; i < count; ++i) {
                int v; double cost; std::cin >> v >> cost;
                if (!std::isfinite(cost) || cost < 0) throw std::runtime_error("invalid price");
                prices.at(v) = cost;
            }
            std::cin >> count;
            for (int i = 0; i < count; ++i) { int v; std::cin >> v; blocked.at(v) = true; }
            if (!std::cin) throw std::runtime_error("truncated request");
            std::vector<double> search_prices = prices;
            if (strategy == 3) {
                // Integer true delays dominate this bounded tie cost. Prefer
                // fewer occupied vertices among equal-delay initial trees.
                double tiny = 1e-4 / g.size();
                for (double& price : search_prices) price += tiny;
            }
            std::vector<int> reuse(g.size(), 0);
            std::set<std::vector<Edge>> seen;
            std::vector<Candidate> candidates;
            for (int attempt = 0; attempt < attempts && static_cast<int>(candidates.size()) < k; ++attempt) {
                Candidate candidate;
                bool success = strategy == 2 || strategy == 3
                    ? build_priced_tree(g, nid, pins, search_prices, blocked, reuse, diversity, seed + attempt, candidate)
                    : strategy == 1
                    ? build_spt(g, nid, pins, prices, blocked, reuse, diversity, seed + attempt, candidate)
                    : build(g, nid, pins, prices, blocked, reuse, diversity, candidate);
                if (!success) break;
                std::set<int> vertices(pins.begin(), pins.end());
                for (auto [a, b] : candidate.edges) { vertices.insert(a); vertices.insert(b); }
                candidate.price = 0;
                for (int v : vertices) candidate.price += prices[v];
                for (int v : vertices) ++reuse[v];
                if (seen.insert(candidate.edges).second) candidates.push_back(std::move(candidate));
            }
            std::sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) {
                double ac = a.delay + a.price, bc = b.delay + b.price;
                if (ac != bc) return ac < bc;
                return a.edges < b.edges;
            });
            std::cout << nid << ' ' << candidates.size() << '\n';
            for (const auto& candidate : candidates) {
                std::cout << candidate.delay << ' ' << candidate.price << ' ' << candidate.edges.size() << '\n';
                for (auto [a, b] : candidate.edges) std::cout << a << ' ' << b << '\n';
            }
            std::cout.flush();
        }
    } catch (const std::exception& error) {
        std::cerr << "router: " << error.what() << '\n';
        return 1;
    }
}
#endif
