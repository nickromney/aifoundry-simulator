# PII detection (Azure AI Language)

The simulator serves the Azure AI Language `analyze-text` call for
`PiiEntityRecognition`, so an APIM policy can find, redact or refuse
personal data in a request without a cloud Language resource. The
apim-simulator `core-pii` policy fragment calls this endpoint with
`send-request` and swaps the request body for `redactedText`.

Microsoft reference:
[Redact PII in text](https://learn.microsoft.com/en-us/azure/ai-services/language-service/personally-identifiable-information/how-to/redact-text-pii)
and [Language data limits](https://learn.microsoft.com/en-us/azure/ai-services/language-service/concepts/data-limits).

## Try it

Start the simulator with `make up`, then send a JSON chat body as the
document text:

```bash
curl -s "http://localhost:8020/language/:analyze-text?api-version=2026-05-01" \
  -H "Ocp-Apim-Subscription-Key: local-foundry-key-alpha" \
  -H "content-type: application/json" \
  -d '{
    "kind": "PiiEntityRecognition",
    "parameters": {"modelVersion": "latest"},
    "analysisInput": {"documents": [{"id": "1", "language": "en",
      "text": "{\"messages\":[{\"role\":\"user\",\"content\":\"Mail jane@contoso.com [simulate:pii=Person:Jane Doe]\"}]}"}]}
  }' | jq .
```

The response lists each entity with its character `offset` and `length`
in the original text, and `redactedText` is the same length as the input
with only entity characters replaced by `*`, so it is still valid JSON:

```json
{
  "kind": "PiiEntityRecognitionResults",
  "results": {
    "documents": [
      {
        "id": "1",
        "redactedText": "{\"messages\":[{\"role\":\"user\",\"content\":\"Mail **************** [simulate:pii=Person:********]\"}]}",
        "entities": [
          {
            "text": "jane@contoso.com",
            "category": "Email",
            "offset": 44,
            "length": 16,
            "confidenceScore": 0.8
          },
          {
            "text": "Jane Doe",
            "category": "Person",
            "offset": 82,
            "length": 8,
            "confidenceScore": 1.0
          }
        ],
        "warnings": []
      }
    ],
    "errors": [],
    "modelVersion": "2026-05-01"
  }
}
```

## Request

| Part | Accepted |
| --- | --- |
| Path | `POST /language/:analyze-text` |
| `api-version` | `2026-05-01` or `2025-11-15-preview`; missing → 400 `MissingApiVersionParameter`, other values → 400 `InvalidApiVersionParameter` |
| Auth | `Ocp-Apim-Subscription-Key`, `api-key` or `Authorization: Bearer`, checked against `auth.api_keys`; failure → 401 |
| `kind` | `PiiEntityRecognition` only; other kinds → 400 |
| `parameters.modelVersion` | `latest` (default) or `2026-05-01` |
| `parameters.piiCategories` | optional list of the categories below (case-insensitive); absent means all |
| `parameters.redactionPolicies` | optional object, or list of objects, with `policyKind` `characterMask` (default), `entityMask` or `noMask`, `redactionCharacter` (default `*`) and optional `entityTypes` to scope a policy |
| `analysisInput.documents` | 1–5 documents, each with a unique string `id`, string `text` and optional `language` |

Redaction policies:

- `characterMask` replaces each entity character with `redactionCharacter`
  (one of `! # $ % & * + - = ? @ ^ _ ~`). Length and offsets are unchanged.
- `entityMask` replaces each entity with `[CATEGORY_N]`, for example
  `[EMAIL_1]`; the same text gets the same number.
- `noMask` returns entities and omits `redactedText`.
- `syntheticReplacement` is rejected with 400 rather than faked.

## Detection

Detection is deterministic and labelled `adapted` in the contract matrix:

| Category | How it is found |
| --- | --- |
| `Email` | address regex |
| `PhoneNumber` | US (`+1 555-010-0199`, `(555) 010-0199`, `555-010-0199`) and UK (`+44 20 7946 0958`, `020 7946 0958`) formats |
| `CreditCardNumber` | 13–19 digits, optional space or dash separators, Luhn-checked |
| `USSocialSecurityNumber` | `NNN-NN-NNNN`, excluding never-issued areas (000, 666, 9xx), group 00 and serial 0000 |
| `UKNationalInsuranceNumber` | two prefix letters (HMRC's excluded letters and prefixes rejected), six digits, suffix A–D; spaces optional |
| `IPAddress` | IPv4 with every octet 0–255 |
| `Person` | simulation trigger only (below) |

Where matches overlap, the earliest wins, then the longest.

### Simulation trigger

A name cannot be found by regex, so force it:

```text
[simulate:pii=Person:Jane Doe]
```

Every occurrence of `Jane Doe` in the document, including the one inside
the trigger, becomes a `Person` entity with confidence 1.0. The same form
works for any supported category, for example
`[simulate:pii=Email:not-an-address]`. The value cannot contain `]`, `"` or
`\`. Triggers follow the resource-wide `content_safety.simulation_triggers`
switch; when it is `false`, no trigger entities are produced.

## Limits and errors

- A document longer than 5,120 characters is not analysed. It appears in
  `results.errors` as
  `{"id": "1", "error": {"code": "InvalidArgument", "message": "Invalid document in request.", "innererror": {"code": "InvalidDocument", "message": "A document within the request was too large to be processed. Limit document size to: 5120 text elements."}}}`,
  and other documents in the request are still processed. An empty
  document gets the same shape with `Document text is empty.`
- More than 5 documents → 400 with `innererror.code`
  `InvalidDocumentBatch`.
- Request validation errors use
  `{"error": {"code": "InvalidRequest", "message": ..., "innererror": {"code": ..., "message": ...}}}`.

A gateway that reads `results.documents[0]` must handle the oversized case:
the document is in `results.errors` and `results.documents` is empty.

## Differences from Azure

- Offsets, lengths and the size limit count Python characters (Unicode code
  points). Azure counts text elements (`TextElements_v8`) by default. They
  agree unless the text has combining sequences or emoji.
  `stringIndexType` is not read.
- Azure's sample responses sometimes report IP addresses as category `IP`;
  the simulator always returns `IPAddress`, the `piiCategories` name.
- Unsupported `api-version` values return 400 here; Azure returns 404.
- The inner-error message text for oversized documents and batch limits is
  modelled on the Text Analytics service; the current Microsoft pages name
  the codes but do not quote the messages.
- Only the categories above are detected. Requesting any other Azure
  category returns 400 rather than an empty result, so a policy cannot
  silently believe it is checking something it is not.
- `domain`, `loggingOptOut`, `stringIndexType` and `excludePiiCategories`
  are ignored if sent.
