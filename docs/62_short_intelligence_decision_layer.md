# Short Intelligence Decision Layer

This diagnostic layer classifies short candidates without enabling short execution. It combines current candidate evidence, short-side historical attribution, inverse-watch hints, and squeeze-risk proxies into one read-only decision record.

Allowed decisions are `block`, `research_only`, `inverse_watch`, `manual_review`, and `paper_short_eligible`. Even when a candidate is theoretically paper eligible, this ticket keeps `paper_short_allowed=false`, `would_submit_order=false`, and `diagnostics_only=true`.
