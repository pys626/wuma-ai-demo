let lastReceipt = null;

// Tab-scoped credential bridge. No passwords, HTML injection or external requests.
export default function(component) {
    const { data, setStateValue } = component;
    if (!data || !["read", "write", "clear"].includes(data.operation)) return;
    let token = "";
    let view = {};
    let status = "ready";
    try {
        if (data.operation === "write") {
            const serialized = JSON.stringify({ token: data.token, view: data.view || {} });
            if (window.sessionStorage.getItem(data.storage_key) !== serialized) {
                window.sessionStorage.setItem(data.storage_key, serialized);
            }
        } else if (data.operation === "clear") {
            window.sessionStorage.removeItem(data.storage_key);
        } else {
            const raw = window.sessionStorage.getItem(data.storage_key);
            if (raw) {
                try {
                    const saved = JSON.parse(raw);
                    token = typeof saved.token === "string" ? saved.token : "invalid";
                    view = saved.view || {};
                } catch {
                    window.sessionStorage.removeItem(data.storage_key);
                    status = "corrupt";
                }
            }
        }
    } catch {
        status = "blocked";
    }
    const receipt = { operation: data.operation, request: data.request, status, token, view };
    const signature = JSON.stringify(receipt);
    // Equal acknowledgments must not cause an endless Streamlit rerun loop.
    if (signature !== lastReceipt) {
        const previousStatus = lastReceipt ? JSON.parse(lastReceipt).status : null;
        lastReceipt = signature;
        // A successful write has no return value for Python. Its acknowledgement
        // would start a redundant rerun and race with the next user interaction.
        if (data.operation === "write" && status === "ready" && previousStatus !== "blocked") return;
        setStateValue("snapshot", receipt);
    }
}
