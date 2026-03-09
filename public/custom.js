/**
 * public/custom.js
 * Polyfill for navigator.clipboard.writeText in non-HTTPS (HTTP) contexts.
 *
 * The Clipboard API requires a secure origin (HTTPS or localhost).
 * When running over plain HTTP (e.g. AWS EC2 without TLS), navigator.clipboard
 * is undefined, causing Chainlit's copy buttons to throw:
 *   "TypeError: Cannot read properties of undefined (reading 'writeText')"
 *
 * This polyfill replaces the missing API with a document.execCommand fallback.
 */

if (!navigator.clipboard) {
    navigator.clipboard = {
        writeText: function (text) {
            return new Promise(function (resolve, reject) {
                try {
                    var textarea = document.createElement("textarea");
                    textarea.value = text;
                    // Keep it out of the viewport
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
        },
        readText: function () {
            return Promise.reject(new Error("clipboard.readText() not supported in HTTP context"));
        }
    };
}
