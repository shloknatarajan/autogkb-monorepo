# PMC Article Fetching: CAPTCHA Findings & Endpoint Recommendations

This document records empirical findings from investigating PMC CAPTCHA / WAF failures in AutoGKB and documents the multi-tier fetching strategy and recommendations for future development.

---

## Background & Incident Summary

On 2026-11-10, 4 triage articles (`42975600` / `PMC13717721`, `42974700` / `PMC13729000`, `42991200` / `PMC13735422`, `42982766` / `PMC13736231`) were submitted within ~2.3 seconds of each other. NCBI's AWS WAF served `Checking your browser - reCAPTCHA` HTML pages with HTTP `200`, which `pubmed_markdown` converted into 114-character Markdown stubs (`# Checking your browser - reCAPTCHA ... /pmc/articles//`) that were saved as `completed` jobs before validation existed.

### Empirical Endpoint Behavior (Tested 2026-11-24)

| Endpoint | Format | CAPTCHA / WAF Behavior | Full-Text Coverage | Notes |
|---|---|---|---|---|
| `https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/?report=classic` (`pubmed_markdown`) | HTML | **High risk** — serves AWS WAF reCAPTCHA on bursts and `403 Forbidden` from cloud/datacenter IPs | Highest (includes articles whose publishers disallow XML downloads) | Canonical converter (`PubMedHTMLToMarkdownConverter`) that our pipeline and viewer were originally tuned against; no built-in timeout or retry in `pubmed_markdown==0.2.5`. |
| `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml` | JATS XML | **None** — official programmatic API, works reliably from cloud IPs | High for PMC OA subset (3/4 affected papers returned 74–135 KB full JATS XML with `<body>` and tables) | Publishers that opt out of XML redistribution (e.g., Europa Group / *EuroIntervention* `PMC13717721`) return metadata + abstract only with comment `<!--The publisher of this article does not allow downloading of the full text in XML form.-->`. Rate limit: 3 req/s without `NCBI_API_KEY`, 10 req/s with `NCBI_API_KEY`. |
| `https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML` | JATS XML | **None** (separate EBI infrastructure) | High for Europe PMC OA subset | Some cloud VMs/networks may experience TLS resets against `www.ebi.ac.uk`; use a short timeout (`15s`) and treat as best-effort fallback. |
| `https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_JSON/{pmcid}/unicode` | BioC JSON | **None** | Delayed / subset only | Returned `No result can be found` for newly published 2026 `PMC137xxxxx` articles; best reserved for supplements (`supplmat.cgi`), which `pubmed_markdown.utils_bioc` already uses. |
| `https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/` | OAI-PMH JATS XML | **Same WAF as `pmc.ncbi.nlm.nih.gov`** (`403 Forbidden` on cloud IPs) | OA subset | Does not bypass `pmc.ncbi.nlm.nih.gov` IP blocks. |

---

## Current Multi-Tier Architecture (`packages/api/src/pmc_fetcher.py`)

To preserve `pubmed_markdown`'s exact HTML-to-Markdown formatting on healthy hosts while automatically recovering from CAPTCHAs and 403 blocks, `fetch_pmcid_markdown()` uses four tiers behind an asyncio concurrency limiter (`PMC_FETCH_MAX_CONCURRENCY=2`) and minimum request spacing (`PMC_FETCH_MIN_INTERVAL_SEC=0.5`):

1. **Tier 1 — `pubmed_markdown` HTML (1 primary attempt)**
   - Preserves `PubMedHTMLToMarkdownConverter` output whenever PMC HTML succeeds cleanly.
   - Immediately validated by `validate_article_markdown()` (length ≥ 500 chars + WAF/CAPTCHA/403 marker checks).
2. **Tier 2 — NCBI E-utilities `efetch` JATS XML (`db=pmc&retmode=xml`)**
   - Automatically triggers if Tier 1 gets a CAPTCHA, `403`, or error.
   - Up to 3 attempts with exponential backoff / `Retry-After` on `429`/`5xx`.
   - Converts JATS XML to structured Markdown via `jats_xml_to_markdown()` (preserving `# Title`, `## Metadata`, `## Abstract`, nested sections, GFM tables, figure captions, references) and appends BioC supplements.
3. **Tier 3 — Europe PMC `fullTextXML`**
   - Triggers when `efetch` fails or the publisher disallows NCBI XML (`<body>` absent).
   - Uses the same `jats_xml_to_markdown()` converter + supplements.
4. **Tier 4 — `pubmed_markdown` HTML retries (2 attempts with exponential backoff + jitter)**
   - Final recovery path for publisher-restricted articles (where neither XML API includes `<body>`) if Tier 1 hit a transient CAPTCHA.
   - If all tiers fail, raises a descriptive `ValueError` instructing the user to upload the PDF directly rather than persisting invalid content.

---

## Recommendations for Future Development

1. **Configure `NCBI_API_KEY` in production (`packages/api` environment)**:
   - Currently `NCBI_EMAIL` is set, while `NCBI_API_KEY` is unset. Setting `NCBI_API_KEY` increases E-utilities quota from 3 req/s to 10 req/s and reduces `429` throttling during `Submit all` and triage backfills.
2. **Consider flipping to XML-first if production runs on a WAF-blocked cloud IP**:
   - On hosts where `pmc.ncbi.nlm.nih.gov` permanently returns `403`, Tier 1 adds one fast failed request per article before Tier 2 succeeds. If logs show persistent `403`s on Tier 1 in production, consider switching to `efetch XML → Europe PMC XML → pubmed_markdown HTML` (or adding an env toggle such as `PMC_FETCH_PREFER_XML=true`).
3. **Consolidate `packages/shared/shared/data_setup/download_article.py`**:
   - Standalone CLI/pipeline downloads in `packages/shared` still call `pubmed_markdown.html_from_pmcid.get_html_from_pmcid()` directly without retries, XML fallback, or CAPTCHA validation. Moving `pmc_fetcher.py` into `packages/shared` (or contributing XML fallback + timeouts/retries upstream into `pubmed_markdown`) will protect CLI and batch evaluation scripts too.
4. **Add timeouts and retries upstream in `pubmed_markdown`**:
   - `pubmed_markdown/html_from_pmcid.py` currently calls `requests.get(url, headers=headers)` with **no `timeout`**, which can hang a worker thread indefinitely if PMC stalls. Passing an explicit timeout upstream (e.g. `timeout=30`) is recommended.
5. **Handle publisher XML opt-outs via PDF Upload**:
   - For publishers that disallow PMC XML redistribution (e.g., *EuroIntervention* / Europa Group) when accessed from a WAF-blocked host, automated scraping cannot bypass AWS WAF reliably — the Literature Triage **Upload PDF** workflow (Datalab PDF → Markdown) is the intended fallback path.
