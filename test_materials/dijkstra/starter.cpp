#include <iostream>
#include <vector>
#include <queue>
#include <functional>
#include <limits>
#include <utility>
using namespace std;

using ll = long long;
struct Edge { int to; ll weight; };
using Graph = vector<vector<Edge>>;

ll shortestPath(const Graph& graph, int source, int target) {
    // TODO: 初始化距离数组和最小优先队列。
    // TODO: 跳过过期状态，并对出边执行松弛。
    // TODO: 返回最短距离，不可达时返回-1。
    return -1;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, m, source, target;
    if (!(cin >> n >> m >> source >> target)) return 0;
    Graph graph(n + 1);
    for (int i = 0; i < m; ++i) {
        int u, v;
        ll weight;
        cin >> u >> v >> weight;
        graph[u].push_back({v, weight});
    }
    cout << shortestPath(graph, source, target) << '\n';
    return 0;
}
