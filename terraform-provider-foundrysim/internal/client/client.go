// Package client talks to the simulator's management API
// (/foundry/management).
//
// It knows nothing about Terraform: it sends and receives JSON objects and
// turns HTTP failures into errors a caller can branch on.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// ErrNotFound reports a 404: the resource does not exist (any more).
var ErrNotFound = errors.New("not found")

// Object is one JSON object, as the management API sends and receives it.
// Numbers decode as json.Number so integers survive the round trip exactly.
type Object = map[string]any

// AdminKeyHeader carries the admin key on every management request.
const AdminKeyHeader = "X-Foundry-Admin-Key"

// APIError is a non-2xx, non-404 response.
type APIError struct {
	Method string
	Path   string
	Status int
	Detail string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s %s: HTTP %d: %s", e.Method, e.Path, e.Status, e.Detail)
}

// Client is safe for concurrent use.
type Client struct {
	base     string
	adminKey string
	http     *http.Client
}

// New returns a client for the simulator at endpoint (for example
// http://localhost:8020). adminKey may be empty when the simulator has no
// admin keys configured.
func New(endpoint, adminKey string, httpClient *http.Client) *Client {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{base: strings.TrimRight(endpoint, "/") + "/foundry/management/", adminKey: adminKey, http: httpClient}
}

// Get reads the resource at path, relative to /foundry/management/.
func (c *Client) Get(ctx context.Context, path string) (Object, error) {
	return c.do(ctx, http.MethodGet, path, nil)
}

// Put creates or replaces the resource at path and returns what was stored.
func (c *Client) Put(ctx context.Context, path string, body Object) (Object, error) {
	if body == nil {
		body = Object{}
	}
	return c.do(ctx, http.MethodPut, path, body)
}

// Delete removes the resource at path.
func (c *Client) Delete(ctx context.Context, path string) error {
	_, err := c.do(ctx, http.MethodDelete, path, nil)
	return err
}

func (c *Client) do(ctx context.Context, method, path string, body Object) (Object, error) {
	var payload io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, fmt.Errorf("encode %s %s: %w", method, path, err)
		}
		payload = bytes.NewReader(encoded)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.base+path, payload)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.adminKey != "" {
		req.Header.Set(AdminKeyHeader, c.adminKey)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%s %s: %w", method, path, err)
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return nil, fmt.Errorf("read %s %s: %w", method, path, err)
	}

	switch {
	case resp.StatusCode == http.StatusNotFound:
		return nil, fmt.Errorf("%s %s: %w", method, path, ErrNotFound)
	case resp.StatusCode >= 300:
		return nil, &APIError{Method: method, Path: path, Status: resp.StatusCode, Detail: detail(raw)}
	case len(bytes.TrimSpace(raw)) == 0:
		return Object{}, nil
	}

	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var out Object
	if err := decoder.Decode(&out); err != nil {
		return nil, fmt.Errorf("decode %s %s: %w", method, path, err)
	}
	return out, nil
}

// detail pulls the human-readable message out of an error body. The
// management API sends {"error": {"code", "message"}}; FastAPI's own errors
// send "detail".
func detail(raw []byte) string {
	var body struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
		Detail any `json:"detail"`
	}
	if json.Unmarshal(raw, &body) == nil {
		if body.Error.Message != "" {
			if body.Error.Code != "" {
				return body.Error.Code + ": " + body.Error.Message
			}
			return body.Error.Message
		}
		switch d := body.Detail.(type) {
		case string:
			return d
		case nil:
		default:
			if encoded, err := json.Marshal(d); err == nil {
				return string(encoded)
			}
		}
	}
	return strings.TrimSpace(string(raw))
}
