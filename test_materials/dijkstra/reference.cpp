#include <iostream>
#include <vector>
#include <queue>
#include <functional>
#include <limits>
#include <utility>
using namespace std;

using ll = long long;
struct Edge {
    int to;
    ll weight;
};
using Graph = vector<vector<Edge>>;
const ll INF = numeric_limits<ll>::max() / 4;

ll shortestPath(const Graph& graph, int source, int target) {
    vector<ll> dist(graph.size(), INF);
    using State = pair<ll, int>;
    priority_queue<State, vector<State>, greater<State>> pending;
    dist[source] = 0;
    pending.push({0, source});

    while (!pending.empty()) {
        State current = pending.top();
        pending.pop();
        ll distance = current.first;
        int u = current.second;
        // 距离改善后，堆里仍可能保留这个节点的旧状态。
        if (distance != dist[u]) {
            continue;
        }
        if (u == target) {
            return distance;
        }
        for (const Edge& edge : graph[u]) {
            ll candidate = distance + edge.weight;
            if (candidate < dist[edge.to]) {
                dist[edge.to] = candidate;
                pending.push({candidate, edge.to});
            }
        }
    }
    return -1;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, m, source, target;
    if (!(cin >> n >> m >> source >> target)) {
        return 0;
    }
    if (n < 1 || n > 2000 || m < 0 || m > 10000 ||
        source < 1 || source > n || target < 1 || target > n) {
        return 0;
    }
    Graph graph(n + 1);
    for (int i = 0; i < m; ++i) {
        int u, v;
        ll weight;
        if (!(cin >> u >> v >> weight) || u < 1 || u > n ||
            v < 1 || v > n || weight < 0 || weight > 1000000000LL) {
            return 0;
        }
        graph[u].push_back({v, weight});
    }
    cout << shortestPath(graph, source, target) << '\n';
    return 0;
}
