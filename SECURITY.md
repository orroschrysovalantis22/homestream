# Security

## Reporting a problem

Please don't open a public issue for a security problem. Use **Report a vulnerability** on this repository's [Security tab](https://github.com/orroschrysovalantis22/homestream/security) instead; only the maintainer sees it. You'll get a reply as soon as possible, and credit in the release notes if you'd like.

Fixes go into the latest release, so please check you're on it first.

## How HomeStream protects you

* **Nothing is exposed to the internet.** Your phone reaches the computer over [Tailscale](https://tailscale.com) (encrypted, private to your devices) or over the local network.
* **Your own devices need no password** only when both ends of the connection are Tailscale addresses *and* the page was opened by address or Tailscale name, so a device on the local Wi-Fi can't pass itself off as one, and a web page can't point its own domain at your computer to get in ("DNS rebinding").
* **Everything else needs the token**, a random 48-character value created at setup and stored only in your settings file. It's compared in constant time and kept in an HttpOnly cookie on the phone.
* **The "Connect a phone" page**, the only page that can show the token, opens only on the computer itself, at `localhost`.
* **Other websites can't press the buttons:** requests the browser labels as coming from another site are refused, and HomeStream's pages can't be shown inside another site's frame.
* **Artwork is only ever served as an image**, whatever the playing app reports.
* **On a local network without Tailscale, traffic isn't encrypted.** Use Tailscale when you can, and don't port-forward HomeStream to the internet.
