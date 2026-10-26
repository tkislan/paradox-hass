# Paradox IP150

A [Home Assistant](https://www.home-assistant.io/) integration for Paradox alarm panels. It talks to the panel through its IP100 or IP150 web module on your local network, with no cloud service, and polls the module's web page once a second.

It gives you:

- an alarm control panel for each area, to arm away, arm home, arm night and disarm;
- a motion sensor for each zone in use;
- a diagnostic sensor that is on while the panel reports a trouble.

It is a port of the Node.js bridge [tkislan/paradox](https://github.com/tkislan/paradox).

## Supported hardware

- IP100 and IP150 web modules with firmware below 4.x.
- IP150 firmware 5.x is **not** supported. It answers the login but returns 404 for the status page, and setup stops with the error `unsupported_firmware`.

Home Assistant 2026.10.0 or later is required.

## Before you start

- **The module allows one web session.** The integration logs in when it starts and keeps that session open. Every login ends the session of whoever was logged in before, so a browser tab on the module's page is logged out when the integration logs in. When you take the session over, the integration logs in again, waiting at least a minute between such logins.
- **Stop the Node bridge** `tkislan/paradox`, and any other client of the module, before you add the integration. Running two of them makes each end the other's session.
- **Repeated failed logins lock the module for 10 minutes.** Check the user name and password on the module's own page before you enter them.

## Installation

Install it through HACS, or through the Marketplace of Home Assistant 2026.11 and later.

**HACS**

1. Open HACS, choose the menu in the top right, then **Custom repositories**. Enter `https://github.com/tkislan/paradox-hass`, choose the type **Integration**, and add it.
2. Search for **Paradox IP150** and download it.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, search for **Paradox IP150** and fill in the fields below.

**Marketplace**

1. Go to **Settings → Marketplace**, search for **Paradox IP150** and install it. The Marketplace lists the integration only once the repository is in the default catalog. Until then, choose **Add from link** and enter `tkislan/paradox-hass`, which needs a connected GitHub account.
2. When the installation ends, the setup page opens, or a repair asks you to restart Home Assistant first. Fill in the fields below.

## Configuration

| Field | What to enter |
| --- | --- |
| Host | The address of the web module on your network, for example `192.168.1.50`. |
| Username | The user name you log in with on the web module's page. |
| Password | The password you log in with on the web module's page. |

Adding the integration logs in to the module and reads its status page. A host that is already set up cannot be added twice.

If the module later rejects the saved credentials, Home Assistant asks you to reauthenticate. Enter the user name and password again. The host stays as it was.

## Entities

Everything belongs to one device, **Paradox**, which links to the module's web page. Entities are named after the zones and areas on the panel, or `Zone N` and `Area N` when the panel has no name.

| Entity | One for | State |
| --- | --- | --- |
| Motion binary sensor | each zone in use | On while the panel reports the zone as open, in alarm, or open with a trouble or an alarm memory. |
| Alarm control panel | each area in use | Disarmed (also while the panel is ready or not ready), armed away, armed home, armed night, arming, pending or triggered. Unknown for a state the integration does not know. |
| `Trouble` binary sensor (diagnostic) | the panel | On while the panel reports any trouble. The attribute `troubles` lists their names. |

An alarm control panel needs no code to arm or disarm. The integration is logged in to the module as the user you configured, so anyone who can control the entity in Home Assistant can arm and disarm that area.

All entities become unavailable while the integration cannot read the status from the module, and come back with the next poll that succeeds.

## Troubleshooting

- **`unsupported_firmware`:** the module answered the login but not the status page. This is IP150 firmware 5.x, which the integration does not support.
- **`cannot_connect` or `invalid_auth`:** check that the host is reachable from Home Assistant and that the user name and password work on the module's page. After repeated failed logins the module locks for 10 minutes.
- **Entities are unavailable while someone else uses the module's web page:** a login by either side ends the other's session. Close the page, or leave it alone until the integration has logged in again.
- **Entities are unavailable now and then while nobody uses the web page:** the module sometimes ignores a new connection. The integration gives up on a connection that is not open after 2 seconds and waits for the next poll. A new login follows only after the polls have gone unanswered for 60 seconds, so that a short stall does not end anyone's session.
- **Diagnostics:** on **Settings → Devices & services**, open **Paradox**, then use the menu of the entry and choose **Download diagnostics**. The file holds the entry, the zone, area and trouble names the panel reported, and the current status. The user name and password are removed, but the host is not, so look through the file before you share it.

## Removal

1. Go to **Settings → Devices & services**, open **Paradox**, and delete the entry from its menu. This logs the integration out of the module.
2. Remove the files. In HACS, remove the repository. In the Marketplace, uninstall the integration when Home Assistant asks after you delete the last entry.
3. If you removed it in HACS, restart Home Assistant.

## License

[MIT](LICENSE)
