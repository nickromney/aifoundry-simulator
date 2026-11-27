package client

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPutSendsAdminKeyAndDecodesNumbersExactly(t *testing.T) {
	var gotKey, gotPath string
	var gotBody Object
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotKey, gotPath = r.Header.Get(AdminKeyHeader), r.URL.EscapedPath()
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &gotBody)
		_, _ = w.Write([]byte(`{"latency_ms": 9007199254740993}`))
	}))
	defer server.Close()

	out, err := New(server.URL+"/", "admin", nil).Put(context.Background(), "deployments/d%201", Object{"model": "m"})
	if err != nil {
		t.Fatal(err)
	}

	if gotKey != "admin" || gotPath != "/foundry/management/deployments/d%201" || gotBody["model"] != "m" {
		t.Fatalf("request = key %q path %q body %v", gotKey, gotPath, gotBody)
	}
	if out["latency_ms"] != json.Number("9007199254740993") {
		t.Fatalf("number lost precision: %#v", out["latency_ms"])
	}
}

func TestNoAdminKeyHeaderWhenNoneIsConfigured(t *testing.T) {
	present := true
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, present = r.Header[http.CanonicalHeaderKey(AdminKeyHeader)]
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	if err := New(server.URL, "", nil).Delete(context.Background(), "blocklists/b"); err != nil {
		t.Fatal(err)
	}
	if present {
		t.Fatal("sent an empty admin key header")
	}
}

func TestErrorsAreClassified(t *testing.T) {
	cases := map[string]struct {
		status int
		body   string
		check  func(error) bool
	}{
		"404 is ErrNotFound": {404, `{"error":{"code":"NotFound","message":"gone"}}`, func(err error) bool { return errors.Is(err, ErrNotFound) }},
		"management error keeps code and message": {409, `{"error":{"code":"Conflict","message":"in use"}}`, func(err error) bool {
			var apiErr *APIError
			return errors.As(err, &apiErr) && apiErr.Status == 409 && apiErr.Detail == "Conflict: in use"
		}},
		"FastAPI detail is used": {422, `{"detail":[{"msg":"Field required"}]}`, func(err error) bool {
			var apiErr *APIError
			return errors.As(err, &apiErr) && apiErr.Detail == `[{"msg":"Field required"}]`
		}},
		"plain text is kept": {500, "boom\n", func(err error) bool {
			var apiErr *APIError
			return errors.As(err, &apiErr) && apiErr.Detail == "boom"
		}},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer server.Close()

			_, err := New(server.URL, "", nil).Get(context.Background(), "deployments/d")

			if err == nil || !tc.check(err) {
				t.Fatalf("unexpected error: %v", err)
			}
		})
	}
}
