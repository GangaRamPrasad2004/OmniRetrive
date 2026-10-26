package middleware

import (
    "net/http"
)

// SecurityHeaders adds common security-related HTTP headers to each response.
func SecurityHeaders(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        // Prevent MIME type sniffing
        w.Header().Set("X-Content-Type-Options", "nosniff")
        // Clickjacking protection
        w.Header().Set("X-Frame-Options", "DENY")
        // Referrer policy
        w.Header().Set("Referrer-Policy", "no-referrer")
        // Content Security Policy – only allow resources from same origin and trusted CDN (adjust as needed)
        w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;")
        // XSS protection (modern browsers ignore but harmless)
        w.Header().Set("X-XSS-Protection", "0")
        next.ServeHTTP(w, r)
    })
}
