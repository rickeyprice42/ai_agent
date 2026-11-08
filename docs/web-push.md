# Web Push notifications

Avelin sends standards-based Web Push using encrypted payloads and a persistent VAPID identity. The server stores device subscriptions and a delivery outbox in SQLite. Delivery runs independently of the chat, automation worker and open browser page.

## Local setup

1. Install `requirements.txt` into the project virtual environment.
2. Run `.venv\Scripts\python.exe scripts\configure_web_push.py` once. Repeating this command preserves the existing private key. Keys remain in the Git-ignored `data/web-push` directory.
3. Start the launcher normally. `/api/notifications/push/config` reports readiness to the authenticated client.
4. In Avelin Settings, enable notifications explicitly. The browser will request permission. Then use the test button.

Optional environment variables are documented in `.env.example`: `WEB_PUSH_ENABLED`, `WEB_PUSH_VAPID_PRIVATE_KEY_FILE`, `WEB_PUSH_VAPID_PUBLIC_KEY`, and `WEB_PUSH_VAPID_SUBJECT`. The public key is normally derived from the private key. Configure an appropriate HTTPS or mailto contact URI before deployment. Keep the private key when moving the server; changing it requires devices to subscribe again.

When the contact is unset or still the legacy `https://avelin.local` placeholder, Avelin uses the existing HTTPS preview origin from `.local/remote-preview.json`. Without a valid contact, push configuration reports the problem instead of claiming readiness. Local contact placeholders can cause Apple to reject VAPID authentication. Apple's [server response documentation](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers) distinguishes authentication errors, expired subscriptions, and provider acceptance.

For an iPhone, use HTTPS, add Avelin to the Home Screen, launch that installed web app and press Enable notifications there. iOS permission must follow a direct user action. These requirements follow [WebKit's Web Push guidance](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).

The local server must remain running and able to reach browser push services. Tailscale access is still needed to open the private Avelin site from a notification. Delivery acceptance by the push provider does not prove a lock-screen notification was displayed. Physical iPhone testing with the PWA closed remains a separate acceptance check.

## Behavior and privacy

- Subscription is opt-in per device and account. Disabling notifications or signing out unsubscribes the current device.
- The queue begins with new notifications after subscription, avoiding historical notification spam.
- Push payloads use generic status text. They contain no chat prompt, tool arguments, file content or raw provider errors.
- Completed direct chat replies also create a notification linked to that exact dialogue. Waiting approvals, cancelled/failed/partial generations and shared messages that did not call Avelin do not create a success notification.
- Permanent expired-endpoint responses disable the subscription. Transient failures retry with backoff and a finite attempt limit.
- Queue claims are durable and recover after a server restart. Web Push is at least once: after a crash at the send/commit boundary a provider may receive the same message again. A stable notification tag lets the browser replace that notification.
- The test button assigns a persisted, idempotent request ID. Its small POST uses `keepalive`; after returning from the background, the PWA reads the same test status using authenticated `GET /api/notifications/push/test/{request_id}`. Losing the HTTP response does not trigger a duplicate test or falsely report push delivery failure.
- Test status `accepted` means the push provider accepted the request, not that the phone displayed it. Authentication failures show a server-delivery problem. Only allowlisted provider reason codes are saved; endpoint tokens and upstream response bodies remain private.
- Only known HTTPS browser push endpoints are accepted; redirects are disabled. VAPID authentication and `aes128gcm` encryption use [pywebpush](https://github.com/web-push-libs/pywebpush).

## Scheduled files and chat titles

New file outputs of an automation are stored under `automations/<automation-id>/<run-id>/...`. Running the same dated template again creates a separate result; an earlier file is preserved. The actual downloadable path is kept in the run report and artifact card.

New chats receive a local topic title immediately, then a short background model request can improve it. Model failure leaves the local title. Manual names are never overwritten. Old named chats are retained because an old automatic name cannot reliably be distinguished from a manual one.
