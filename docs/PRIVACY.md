# Privacy

Courtside's published dashboard is read-only. It does not require an in-product
profile and does not collect sportsbook credentials, payment information,
location, contacts, advertising identifiers, or behavioral analytics.

When optional access protection is enabled, Cloudflare processes the email used
for its login challenge under the Cloudflare account's configuration. When email
alerts are enabled, the chosen email provider processes the destination address;
the Courtside ledger stores only a one-way address hash, delivery status, and
provider receipt ID. Browser preferences such as theme, timezone, bookmaker, and
layout stay in local storage on that device.

The public snapshot contains game schedules, model estimates, market observations,
paper decisions, aggregate performance, model metadata, and operational freshness.
The private ledger, runtime model, API keys, and email credentials are not
published. Remove access and notification secrets to stop those services.
