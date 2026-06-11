/**
 * public/custom.js
 * Polyfill for navigator.clipboard.writeText in non-HTTPS (HTTP) contexts.
 *
 * The Clipboard API requires a secure origin (HTTPS or localhost).
 * When running over plain HTTP (e.g. AWS EC2 without TLS), navigator.clipboard
 * may exist but all operations are blocked because window.isSecureContext is false.
 *
 * This polyfill overrides writeText with a document.execCommand fallback whenever
 * the page is not in a secure context, regardless of whether the API object exists.
 */

function execCommandCopyFallback(text) {
    return new Promise(function (resolve, reject) {
        try {
            var textarea = document.createElement("textarea");
            textarea.value = text;
            textarea.style.position = "fixed";
            textarea.style.top = "-9999px";
            textarea.style.left = "-9999px";
            textarea.style.opacity = "0";
            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();
            var ok = document.execCommand("copy");
            document.body.removeChild(textarea);
            if (ok) {
                resolve();
            } else {
                reject(new Error("execCommand('copy') returned false"));
            }
        } catch (err) {
            reject(err);
        }
    });
}

if (!window.isSecureContext) {
    if (!navigator.clipboard) {
        navigator.clipboard = {};
    }
    navigator.clipboard.writeText = execCommandCopyFallback;
    navigator.clipboard.readText = function () {
        return Promise.reject(new Error("clipboard.readText() not supported in HTTP context"));
    };
}
