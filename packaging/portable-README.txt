dmslog portable (Windows x64)
=============================

Log analyzer and viewer for ADMS adapter logs. This folder is the whole
application: embedded Python, dmslog.pyz and dmslog.bat. No installation, no
administrator rights, no internet access needed. Copy the folder anywhere.

Contents
  dmslog.bat   launcher (use this one)
  dmslog.pyz   the application
  python\      embedded Python 3.13 (used by dmslog.bat; leave it alone)
  knowledge\   optional knowledge pack (only in internal builds; see below)
  README.txt   this file

Knowledge pack (optional)
  The viewer works without it. Without a pack, features that name parameters and
  error codes from the vendor documentation show "knowledge pack not installed"
  (dmslog.bat info says whether one was found and where). dmslog looks for it, in
  this order: --pack-dir DIR (or the DMSLOG_PACK_DIR variable), then
  %LOCALAPPDATA%\dmslog\knowledge\pack, then knowledge\pack in this folder.

PRIVATE USE ONLY (when a knowledge\pack folder is present)
  The pack is derived from Schneider Electric documentation and is for
  internal/private use. A folder or zip that contains knowledge\pack must not be
  redistributed or published. The public build of this folder does not contain it.

Starting the viewer
  Double-click dmslog.bat, or run it from a terminal opened in this folder:

    dmslog.bat

  A console window shows a local URL with an access token and opens your
  default browser (Edge or Chrome). Keep the console window open while you use
  the viewer. If the browser does not open, copy the URL from the console.

Loading logs
  In the viewer: create a case and add the ERS zip, or the local Log folder
  (for example C:\SchneiderElectricData\...\Log), by its path. A case holds one
  ERS. Files are read in place, never extracted or modified.

  From the command line (index first, then open the case in the viewer):

    dmslog.bat ingest C:\path\to\ERS.zip
    dmslog.bat ingest C:\path\to\Log --case my-case
    dmslog.bat serve

  To follow a live Log folder while the adapters write:

    dmslog.bat serve --watch C:\path\to\Log

  All commands and options: dmslog.bat --help (or dmslog.bat COMMAND --help).
  Version and environment check: dmslog.bat --version, dmslog.bat info.

Where your data is
  Indexes and settings are kept in %LOCALAPPDATA%\dmslog (one folder per case;
  deleting a case deletes its index). Use --data-dir DIR on any command to keep
  them elsewhere, for example an encrypted drive. Indexes contain log content:
  treat them like the logs themselves.

Privacy
  Nothing leaves this PC. The server listens on 127.0.0.1 only, requires the
  access token, makes no network requests and sends no telemetry.

Stopping
  Press Ctrl+C in the console window, or close it. Then close the browser tab.

Requirements
  Windows 10/11 x64 and Microsoft Edge or Google Chrome (current versions).
