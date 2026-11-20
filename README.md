# Kodi for Home Assistant

A custom integration that replaces the built-in [Kodi integration](https://www.home-assistant.io/integrations/kodi) of Home Assistant. It carries the same code, plus changes that are on their way into Home Assistant Core but are not released there yet. Every change here is made so it can go to [home-assistant/core](https://github.com/home-assistant/core) as a small pull request later.

It uses the domain `kodi`, so your config entries, entities, device triggers, the `kodi_keypress` event and the `kodi.add_to_playlist` and `kodi.call_method` actions stay the same. Going back to the built-in integration needs a few steps, see [Removal](#removal).

## What is different from the built-in integration

It is the Kodi integration of the Home Assistant Core dev branch with:

- a fix for a refresh that went on after Kodi stopped playing or quit ([home-assistant/core#192353](https://github.com/home-assistant/core/pull/192353))
- the connection to Kodi owned by the config entry instead of the media player, which closes the connection on unload and on stop, and no longer reconnects an entry that was unloaded during startup
- a check of the username and password at setup, after a failed poll of an entry without WebSocket, and while the WebSocket is connected, with at most one HTTP ping a minute; when the web server of Kodi rejects them, Home Assistant asks for new credentials
- one info message when Kodi becomes unavailable and one when it is available again, so a restart of Kodi no longer leaves a warning in the log
- the setup refuses a Kodi that is already set up at the same host and port, and discovery leaves a Kodi alone that was added by hand at the address it announces
- a description for every field of the setup, with the current names of the Kodi settings
- `kodi.add_to_playlist` raises an error for input it can not use, like a name that matches nothing in the music library, where it used to add the first item of the library

The [roadmap](#roadmap) lists what comes next.

## Requirements

- Home Assistant 2026.11.0 or newer.
- In Kodi, under **Settings** > **Services** > **Control**:
  - **Allow remote control via HTTP**: the integration sends its requests to the Kodi web server.
  - **Allow remote control from applications on this system**, and **Allow remote control from applications on other systems** when Home Assistant runs on another system: needed for the WebSocket connection, which tells Home Assistant right away when playback changes. If you leave the WebSocket port empty in the setup, the integration polls Kodi over HTTP every 10 seconds instead.

## Installation

The integration is installed from the Marketplace of Home Assistant, as a custom repository. Adding a custom repository needs a GitHub connection in the Marketplace.

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=olympia&repository=ha-kodi&category=integration)

1. Open the link above, and add the repository when the Marketplace asks.
2. Install it. The Marketplace asks you to confirm, because the integration replaces a built-in one.
3. Restart Home Assistant, so it loads this integration in place of the built-in one.

Home Assistant then logs a warning that it found the custom integration `kodi`. That is expected.

## Configuration

Kodi is found on the network through zeroconf, or added by hand under **Settings** > **Devices & services** > **Add integration** > **Kodi**. The setup asks for:

- **Host**: the hostname or IP address of the system running Kodi.
- **Port**: the port of the Kodi web server, set under **Settings** > **Services** > **Control** in Kodi, 8080 by default.
- **Uses an SSL certificate**: turn on when you reach Kodi over HTTPS, like with **Enable SSL** on in Kodi or behind a reverse proxy with HTTPS.
- **Username** and **Password**: only asked when the Kodi web server requires a login. Kodi uses the username `kodi` by default.
- **Port** of the WebSocket: only asked when the WebSocket connection fails at 9090, the default of Kodi. Leave it empty to use HTTP only.

A Kodi that is already set up at the same host and port can not be added again.

## Known limitations

- A Kodi added by hand keeps the host and port it was added with, and does not follow an address change that discovery reports. There is no way yet to change them, a reconfigure step is on the roadmap.
- The duplicate check compares the host and port as entered. The same Kodi under another name, like its hostname and its IP address, can be added twice.
- After you delete a Kodi that was added by hand, discovery offers it again only at its next announcement on the network. You can add it by hand again right away.

## Removal

To remove Kodi from Home Assistant, delete it under **Settings** > **Devices & services** > **Kodi**, in the menu of each Kodi.

To go back to the built-in Kodi integration:

1. Delete every Kodi under **Settings** > **Devices & services** > **Kodi**. The Marketplace does not uninstall an integration that still has a config entry, even when a built-in integration would take it over. This removes the devices and entities of Kodi.
2. Uninstall the repository in the Marketplace.
3. Restart Home Assistant.
4. Add Kodi again, now with the built-in integration.
5. Check the automations and scripts that use a Kodi device, a device trigger or a Kodi entity.

## Roadmap

Each item is a separate change, in the order of the planned core pull requests:

- the media player becomes unavailable when Kodi is unreachable
- errors of media player commands and actions are raised instead of only logged
- a reconfigure step to change the host, port and other settings of a Kodi
- sensors for the video resolution, HDR format and HDR hints of what Kodi plays
- strict typing

## Development

The tests use [pytest-homeassistant](https://pypi.org/project/pytest-homeassistant/), the test package of Home Assistant Core. With [uv](https://docs.astral.sh/uv/):

```shell
uv venv --python 3.14
uv pip install -r requirements_test.txt
pytest --cov --cov-report=json
python -m script.check_coverage
```

The settings of ruff, mypy, pytest and coverage follow home-assistant/core, see `pyproject.toml` and `tests/ruff.toml`.

### Translations

`custom_components/kodi/strings.json` is the source of the texts, as in core. Home Assistant only compiles it for integrations in core, so the files in `custom_components/kodi/translations` are generated:

```shell
python -m script.gen_translations
```

`en.json` gets `strings.json` with every reference replaced. The other languages come from the Kodi translations of the installed Home Assistant, for every key whose English text is unchanged here. A new or changed key shows in English until it is translated in core.

### Differences from the code in core

These are the only differences from what the code would be in core:

- `manifest.json` has `version` and `issue_tracker`, and `documentation` and `codeowners` point to this repository.
- `manifest.json` asks for a minimum version of `pykodi` instead of the exact pin of core, because hassfest refuses a custom integration that pins a package Home Assistant depends on.
- `translations/` is generated, see above. Core generates it at build time.
- `brand/` holds the images of the [brands repository](https://github.com/home-assistant/brands), because a custom integration ships its own.
- The tests import `custom_components.kodi` instead of `homeassistant.components.kodi`, with the imports sorted again by ruff.

## License

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). The code started as a copy of the Kodi integration of Home Assistant Core.
