/**
 * Client-side Authentication & Password Hashing Module
 * Computes SHA-256 hash using the browser's native Web Cryptography API.
 * Plaintext passwords never leave the client device or travel over the network.
 */

// Compute SHA-256 hash of a string using WebCrypto API
async function hashPassword(plainText) {
    const encoder = new TextEncoder();
    const data = encoder.encode(plainText);
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

// Global checkLogin function for compatibility with any login form
async function checkLogin() {
    const userInput = document.getElementById("username");
    const passInput = document.getElementById("password");
    
    if (!userInput || !passInput) return;
    
    const username = userInput.value.trim();
    const password = passInput.value;
    
    if (!username || !password) {
        alert("Please enter both username and password.");
        return;
    }
    
    try {
        // Hash the password with SHA-256 before transmitting
        const passwordHash = await hashPassword(password);
        const nextUrl = new URLSearchParams(window.location.search).get("next") || "/";
        
        const response = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                username: username,
                password_hash: passwordHash,
                next: nextUrl
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            if (result.role === "admin") {
                window.location.href = result.redirect || "/admin";
            } else {
                window.location.href = result.redirect || "/";
            }
        } else {
            alert(result.error || "Incorrect username or password, try again!");
        }
    } catch (err) {
        alert("Authentication request failed: " + err.message);
    }
}