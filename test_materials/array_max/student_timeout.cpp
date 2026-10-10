#include <iostream>
using namespace std;

int main() {
    int n;
    if (!(cin >> n)) {
        return 0;
    }

    // 故意保留的测试错误：永远不结束，也不输出内容。
    // volatile 保留循环中的读写，避免优化器删除循环体。
    volatile unsigned int ticks = 0;
    while (true) {
        ticks = ticks + 1u;
    }

    return 0;
}
