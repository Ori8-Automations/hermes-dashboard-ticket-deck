/*
 * Hermes Ticket Deck — read-only Zammad ticketing dashboard (frontend).
 *
 * No build step: a plain IIFE that renders with the React instance provided by
 * the Hermes Plugin SDK. All ticket/article text is rendered into React text
 * nodes (never raw HTML). No write actions, no attachment downloads.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var plugins = window.__HERMES_PLUGINS__;
  if (!SDK || !plugins || !SDK.React) return;

  var React = SDK.React;
  var h = React.createElement;
  var hooks = SDK.hooks || React;
  var useEffect = hooks.useEffect;
  var useMemo = hooks.useMemo;
  var useState = hooks.useState;

  var API = "/api/plugins/hermes-ticket-deck";

  function fetchJSON(path) {
    return fetch(API + path, { credentials: "same-origin" }).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (body) { throw new Error(res.status + ": " + body); });
      }
      return res.json();
    });
  }

  function fmt(value) {
    if (value === null || value === undefined || value === "") return "—";
    return String(value);
  }

  function safeUrl(url) {
    if (!url || typeof url !== "string") return null;
    try {
      var parsed = new URL(url, window.location.origin);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
    } catch (_e) { /* ignore */ }
    return null;
  }

  function Metric(props) {
    return h("div", { className: "htd-metric" },
      h("span", { className: "htd-metric-label" }, props.label),
      h("strong", { className: "htd-metric-value" }, props.value),
      props.hint ? h("small", { className: "htd-muted" }, props.hint) : null
    );
  }

  function TicketRow(props) {
    var ticket = props.ticket;
    return h("button", { className: "htd-ticket", onClick: function () { if (props.onOpen) props.onOpen(ticket); } },
      h("div", { className: "htd-ticket-main" },
        h("div", { className: "htd-ticket-title" },
          h("span", { className: "htd-ticket-number" }, "#" + fmt(ticket.number)),
          h("strong", null, fmt(ticket.title))
        ),
        h("div", { className: "htd-ticket-meta" },
          h("span", null, fmt(ticket.group)),
          h("span", null, fmt(ticket.state)),
          h("span", null, fmt(ticket.priority)),
          h("span", null, "updated " + fmt(ticket.updated_at))
        )
      ),
      h("div", { className: "htd-ticket-badges" },
        ticket.unassigned ? h("span", { className: "htd-badge warn" }, "unassigned") : null,
        ticket.escalated ? h("span", { className: "htd-badge danger" }, "escalated") : null,
        h("span", { className: "htd-badge" }, "articles " + fmt(ticket.article_count))
      )
    );
  }

  function Article(props) {
    var article = props.article;
    return h("article", { className: "htd-article" },
      h("div", { className: "htd-article-meta" },
        h("strong", null, fmt(article.sender || article.from || article.type)),
        h("span", null, fmt(article.type)),
        article.internal ? h("span", { className: "htd-badge warn" }, "internal") : null,
        h("span", null, fmt(article.created_at))
      ),
      article.subject ? h("h3", null, article.subject) : null,
      h("pre", { className: "htd-article-body" }, fmt(article.body)),
      article.attachment_count ? h("p", { className: "htd-muted" }, "Attachments: " + article.attachment_count + " (not shown)") : null
    );
  }

  function TicketDeck() {
    var hs = useState(null); var health = hs[0], setHealth = hs[1];
    var su = useState(null); var summary = su[0], setSummary = su[1];
    var tk = useState([]); var tickets = tk[0], setTickets = tk[1];
    var dt = useState(null); var detail = dt[0], setDetail = dt[1];
    var dl = useState(false); var detailLoading = dl[0], setDetailLoading = dl[1];
    var pr = useState("open"); var preset = pr[0], setPreset = pr[1];
    var gf = useState(""); var groupFilter = gf[0], setGroupFilter = gf[1];
    var ld = useState(true); var loading = ld[0], setLoading = ld[1];
    var er = useState(null); var error = er[0], setError = er[1];

    var ticketQuery = useMemo(function () {
      var params = new URLSearchParams({ preset: preset, limit: "25" });
      if (groupFilter) params.set("group", groupFilter);
      return "/tickets?" + params.toString();
    }, [preset, groupFilter]);

    function loadAll() {
      setLoading(true);
      setError(null);
      // allSettled: a failure in one call still renders the others.
      Promise.allSettled([
        fetchJSON("/health"),
        fetchJSON("/summary"),
        fetchJSON(ticketQuery)
      ]).then(function (results) {
        var errs = [];
        if (results[0].status === "fulfilled") setHealth(results[0].value); else errs.push(results[0].reason);
        if (results[1].status === "fulfilled") setSummary(results[1].value); else errs.push(results[1].reason);
        if (results[2].status === "fulfilled") setTickets((results[2].value && results[2].value.tickets) || []);
        else { setTickets([]); errs.push(results[2].reason); }
        setDetail(null);
        setError(errs.length ? String((errs[0] && errs[0].message) || errs[0]) : null);
      }).finally(function () { setLoading(false); });
    }

    useEffect(loadAll, [ticketQuery]);

    function openTicket(ticket) {
      if (!ticket || !ticket.id) return;
      setDetailLoading(true);
      setError(null);
      fetchJSON("/tickets/" + encodeURIComponent(ticket.id)).then(function (data) {
        setDetail(data);
      }).catch(function (err) {
        setError(String(err.message || err));
      }).finally(function () { setDetailLoading(false); });
    }

    var counts = summary && summary.counts ? summary.counts : {};
    var groups = summary && summary.facets ? summary.facets.open_by_group || {} : {};
    var uiUrl = health && safeUrl(health.ui_url);

    return h("div", { className: "htd-root" },
      h("header", { className: "htd-header" },
        h("div", null,
          h("p", { className: "htd-eyebrow" }, "Read-only · API-backed" + (health && health.mode === "mock" ? " · mock" : "")),
          h("h1", { className: "htd-title" }, "Ticket Deck"),
          h("p", { className: "htd-muted" }, "Zammad ticket visibility inside the dashboard. No writes, no attachment downloads, read-only detail.")
        ),
        h("div", { className: "htd-actions" },
          h("button", { className: "htd-btn", onClick: loadAll }, loading ? "Refreshing…" : "Refresh"),
          uiUrl ? h("a", { className: "htd-btn", href: uiUrl, target: "_blank", rel: "noreferrer noopener" }, "Open full Zammad UI") : null
        )
      ),

      error ? h("div", { className: "htd-error" }, error) : null,

      h("section", { className: "htd-status" },
        h("span", null, "API: ", h("strong", null, health ? health.status : loading ? "checking" : "unknown")),
        h("span", null, "User: ", h("strong", null, health && health.zammad_user ? fmt(health.zammad_user.login) : "—")),
        h("span", null, "Checked: ", h("strong", null, summary ? fmt(summary.checked_at) : "—"))
      ),

      h("section", { className: "htd-metrics" },
        h(Metric, { label: "Open visible", value: counts.open_visible_capped || 0, hint: "capped read" }),
        h(Metric, { label: "Unassigned", value: counts.open_unassigned_visible_capped || 0, hint: "owner unset/system" }),
        h(Metric, { label: "Escalated", value: counts.open_escalated_visible_capped || 0, hint: "escalation timestamps" }),
        h(Metric, { label: "High priority", value: counts.high_priority_visible_capped || 0, hint: "priority 3 high" })
      ),

      h("section", { className: "htd-layout" },
        h("div", { className: "htd-panel" },
          h("h2", null, "Open by group"),
          groupFilter ? h("button", { className: "htd-clear-group", onClick: function () { setGroupFilter(""); } }, "Clear group filter: " + groupFilter) : null,
          Object.keys(groups).length ? h("div", { className: "htd-group-list" },
            Object.keys(groups).sort().map(function (name) {
              return h("button", {
                className: "htd-group" + (groupFilter === name ? " is-active" : ""),
                key: name,
                onClick: function () { setGroupFilter(groupFilter === name ? "" : name); }
              }, h("span", null, name), h("strong", null, groups[name]));
            })
          ) : h("p", { className: "htd-muted" }, "No group counts loaded yet.")
        ),
        h("div", { className: "htd-panel" },
          h("div", { className: "htd-panel-head" },
            h("h2", null, groupFilter ? "Tickets · " + groupFilter : "Tickets"),
            h("div", { className: "htd-presets" },
              ["open", "new", "pending", "high"].map(function (name) {
                return h("button", {
                  key: name,
                  className: preset === name ? "is-active" : "",
                  onClick: function () { setPreset(name); }
                }, name);
              })
            )
          ),
          loading ? h("p", { className: "htd-muted" }, "Loading tickets…") : null,
          tickets.length ? h("div", { className: "htd-ticket-list" },
            tickets.map(function (ticket) { return h(TicketRow, { key: ticket.id || ticket.number, ticket: ticket, onOpen: openTicket }); })
          ) : !loading ? h("p", { className: "htd-muted" }, "No tickets returned for this preset.") : null
        )
      ),

      detail ? h("section", { className: "htd-detail" },
        h("div", { className: "htd-detail-head" },
          h("div", null,
            h("p", { className: "htd-eyebrow" }, "Read-only ticket detail"),
            h("h2", null, "#" + fmt(detail.ticket && detail.ticket.number) + " · " + fmt(detail.ticket && detail.ticket.title))
          ),
          h("button", { className: "htd-btn", onClick: function () { setDetail(null); } }, "Close")
        ),
        detailLoading ? h("p", { className: "htd-muted" }, "Loading ticket…") : null,
        h("div", { className: "htd-detail-meta" },
          h("span", { className: "htd-badge" }, fmt(detail.ticket && detail.ticket.group)),
          h("span", { className: "htd-badge" }, fmt(detail.ticket && detail.ticket.state)),
          h("span", { className: "htd-badge" }, fmt(detail.ticket && detail.ticket.priority)),
          h("span", { className: "htd-badge" }, "articles " + ((detail.articles || []).length))
        ),
        h("div", { className: "htd-articles" },
          (detail.articles || []).map(function (article) { return h(Article, { key: article.id, article: article }); })
        )
      ) : null,

      h("footer", { className: "htd-footer" },
        "Read-only: ticket metadata and sanitized article text only; no writes, no attachment downloads, no raw HTML."
      )
    );
  }

  plugins.register("hermes-ticket-deck", TicketDeck);
})();
