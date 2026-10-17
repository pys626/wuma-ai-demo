#include <iostream>
using namespace std;

int main() {
    int n;
    if (!(cin >> n) || n < 1 || n > 100) {
        return 0;
    }

    int a[100];
    for (int i = 0; i < n; ++i) {
        if (!(cin >> a[i])) {
            return 0;
        }
    }

    int maximum = a[0];
    for (int i = 1; i < n; ++i) {
        if (a[i] > maximum) {
            maximum = a[i];
        }
    }

    // 故意保留的测试错误：下一行缺少分号。
    cout << maximum << '\n'
    return 0;
}
