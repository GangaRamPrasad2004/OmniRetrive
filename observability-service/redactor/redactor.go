package redactor

import (
	"fmt"
	"regexp"
	"strings"
)

var (
	bearerRegex   = regexp.MustCompile(`(?i)Bearer\s+([A-Za-z0-9_\-\.]+)`)
	hfTokenRegex  = regexp.MustCompile(`(?i)hf_[A-Za-z0-9_\-]{16,}`)
	skTokenRegex  = regexp.MustCompile(`(?i)sk-[A-Za-z0-9]{20,}`)
	urlPassRegex  = regexp.MustCompile(`(?i)://([^:\s/@]+):([^@\s]+)@`)
	keyValRegex   = regexp.MustCompile(`(?i)(api[_-]?key|token|secret|password|passwd|auth)\s*[:=]\s*['"]?([A-Za-z0-9_\-\.]{4,})['"]?`)
)

// RedactSecrets scans a string and scrubs all sensitive tokens, passwords, and keys.
func RedactSecrets(text string) string {
	if text == "" {
		return ""
	}
	res := bearerRegex.ReplaceAllString(text, "Bearer [REDACTED]")
	res = hfTokenRegex.ReplaceAllString(res, "hf_[REDACTED]")
	res = skTokenRegex.ReplaceAllString(res, "sk-[REDACTED]")
	res = urlPassRegex.ReplaceAllString(res, "://${1}:[REDACTED]@")
	res = keyValRegex.ReplaceAllString(res, "${1}=[REDACTED]")
	return res
}

// SanitizePayload recursively scrubs map structures and truncates large document text fields
// to prevent proprietary document contents from persisting in log streams.
func SanitizePayload(data map[string]interface{}, maxTextLen int) map[string]interface{} {
	if data == nil {
		return nil
	}
	if maxTextLen <= 0 {
		maxTextLen = 150
	}
	sanitized := make(map[string]interface{}, len(data))

	for k, v := range data {
		lowerK := strings.ToLower(k)

		if strings.Contains(lowerK, "password") ||
			strings.Contains(lowerK, "token") ||
			strings.Contains(lowerK, "secret") ||
			strings.Contains(lowerK, "api_key") ||
			strings.Contains(lowerK, "auth") {
			sanitized[k] = "[REDACTED]"
			continue
		}

		if (lowerK == "text" || lowerK == "chunk" || lowerK == "content" || lowerK == "document_text" || lowerK == "raw_content") {
			if strVal, ok := v.(string); ok {
				if len(strVal) > maxTextLen {
					sanitized[k] = fmt.Sprintf("%s... [truncated %d chars]", strVal[:maxTextLen], len(strVal))
				} else {
					sanitized[k] = RedactSecrets(strVal)
				}
				continue
			}
		}

		switch val := v.(type) {
		case map[string]interface{}:
			sanitized[k] = SanitizePayload(val, maxTextLen)
		case []interface{}:
			sanitized[k] = sanitizeSlice(val, maxTextLen)
		case string:
			sanitized[k] = RedactSecrets(val)
		default:
			sanitized[k] = val
		}
	}

	return sanitized
}

func sanitizeSlice(slice []interface{}, maxTextLen int) []interface{} {
	out := make([]interface{}, len(slice))
	for i, item := range slice {
		switch v := item.(type) {
		case map[string]interface{}:
			out[i] = SanitizePayload(v, maxTextLen)
		case []interface{}:
			out[i] = sanitizeSlice(v, maxTextLen)
		case string:
			out[i] = RedactSecrets(v)
		default:
			out[i] = v
		}
	}
	return out
}
