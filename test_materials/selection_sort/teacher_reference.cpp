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

    for (int i = 0; i < n - 1; ++i) {
        int minIndex = i;
        for (int j = i + 1; j < n; ++j) {
            if (a[j] < a[minIndex]) {
                minIndex = j;
            }
        }
        if (minIndex != i) {
            int temp = a[i];
            a[i] = a[minIndex];
            a[minIndex] = temp;
        }
    }

    for (int i = 0; i < n; ++i) {
        if (i > 0) {
            cout << ' ';
        }
        cout << a[i];
    }
    cout << '\n';
    return 0;
}
