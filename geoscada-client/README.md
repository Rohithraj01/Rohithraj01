# Geo SCADA client by IP (instead of Remote Desktop)

Today, to check a value or change a setpoint, you open a **Remote Desktop session** on
the Geo SCADA server and work there. This tool does those jobs from your own PC. It
connects straight to the server's IP address with the **Geo SCADA .NET Client API**
(`ClearSCADA.Client.dll`), which speaks the same protocol as ViewX.

| | Remote Desktop | `geoscada` |
|---|---|---|
| Port to open | TCP 3389 (a whole Windows desktop) | TCP 5481 (Geo SCADA client port only) |
| Login | Windows account on the server | Geo SCADA user, with the same permissions and audit trail as ViewX |
| Server load | one Windows session per person | one light client connection |
| Scripting | none | every command works in batch files, Task Scheduler and PowerShell |
| Redundant pair | connect to each server yourself | `--host main,standby` picks whichever answers |

## What it can do

```
geoscada status                              log on and show which server answered
geoscada browse [group]                      list what is inside a group
geoscada read <object> [property]            read a value (default CurrentValue)
geoscada watch <object> [property] [secs]    keep reading until Ctrl+C
geoscada write <object> <property> <value>   set a value          (needs --allow-write)
geoscada call <object> <method> [args...]    run a method, e.g. Override (needs --allow-write)
geoscada query "<sql>"                       any Geo SCADA SQL query
geoscada shell                               log on once, then type commands
```

Commands that change the live system will not run unless you add `--allow-write`.

## Build (once, on a PC that has Geo SCADA or its client tools installed)

You need the .NET SDK, plus `ClearSCADA.Client.dll` from your Geo SCADA install. That
file is Schneider Electric's, so it is not in this repository.

```powershell
cd geoscada-client
dotnet build -c Release
# Geo SCADA installed somewhere else:
dotnet build -c Release -p:GeoScadaDir="D:\Schneider Electric\ClearSCADA"
```

The build copies `ClearSCADA.Client.dll` next to `geoscada.exe` in `bin\Release\net48\`,
so you can copy that whole folder to any Windows PC on the SCADA network. Use the DLL
from the same Geo SCADA version as the server.

## Use

```powershell
# Set these once per window (or pass --host / --user each time)
$env:GEOSCADA_HOST = "10.20.0.15,10.20.0.16"   # main,standby
$env:GEOSCADA_USER = "engineer"

geoscada status
geoscada browse "Site A"
geoscada read "Site A.Pump 1.Run Status"
geoscada read "Site A.Pump 1.Run Status" CurrentQuality
geoscada watch "Site A.Reservoir.Level" CurrentValue 5
geoscada query "SELECT FullName, CurrentValue, CurrentTime FROM CDBPoint WHERE FullName LIKE 'Site A.%'"
geoscada --allow-write write "Site A.Setpoint" CurrentValue 42.5
```

You are asked for the password, and it is not shown as you type. For scheduled jobs you
can set `GEOSCADA_PASSWORD` instead. Use a Geo SCADA user that has only the rights the
job needs. Never put the password on the command line.

## Setting up the network (server side)

1. On the Geo SCADA server, allow **inbound TCP 5481** in Windows Firewall. Allow it only
   from the engineering subnet or a jump host, not from everywhere.
2. In **Server Configuration Tool → Network**, check that the client port is 5481 (if you
   use another port, pass `--port`).
3. Make sure the Geo SCADA user exists and has the permissions you need on the groups
   you will read or write (Database Manager → Security).
4. Once everyone uses this tool or ViewX, you can turn off Remote Desktop to the server,
   or limit it to administrators.

If the link leaves the trusted SCADA network, put it inside a VPN. The standard client
connection is not meant to be exposed to the internet.

## Error 0x80090331

```
Could not connect to Geo SCADA at 192.168.8.142: ... unknown status code 0x80090331
```

`0x80090331` is a Windows TLS error (`SEC_E_ALGORITHM_MISMATCH`): the PC and the server
have **no TLS version or cipher in common**, so the encrypted connection is refused
before Geo SCADA even sees a login. First find out what the server speaks:

```powershell
python tls_probe.py 192.168.8.142            # tests ports 443 and 5481
python tls_probe.py 192.168.8.142 <port>     # or the port your client uses
```

**The probe says the server only speaks TLS 1.0/1.1.** This is the usual case. The
original WebX HTTPS server (ClearSCADA and Geo SCADA 2020 and older) only supports
TLS 1.0/1.1, and current Windows, or company Group Policy, turns those off. Fixes, best
first:

1. Don't go through WebX at all. Connect with the .NET Client API on port 5481 (this
   tool, or `ClearSCADA.Client.dll` from your own code). It doesn't use the WebX HTTPS
   server.
2. On Geo SCADA 2021 or newer, connect to the **IIS reverse proxy** (HTTPS, port 443 by
   default, TLS 1.2) instead of the WebX HTTPS port. If you connect by IP and the
   certificate is issued to the server's name, use the name instead.
3. Use the plain HTTP WebX port. This works, but the password crosses the network
   unencrypted, so only do it on an isolated SCADA network.

Turning TLS 1.0 back on for the whole PC also "fixes" it, but it weakens every program
on that PC, so avoid it.

**The probe says the server accepts TLS 1.2 or newer.** Then the client side is what's
holding it back:

- **.NET Framework programs**, including Python code that loads `ClearSCADA.Client.dll`
  through pythonnet, may only offer TLS 1.0 unless strong crypto is turned on. In an
  admin PowerShell, then restart the program:

  ```powershell
  foreach ($k in 'HKLM:\SOFTWARE\Microsoft\.NETFramework\v4.0.30319',
                 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\.NETFramework\v4.0.30319') {
      Set-ItemProperty $k SchUseStrongCrypto 1 -Type DWord
      Set-ItemProperty $k SystemDefaultTlsVersions 1 -Type DWord
  }
  ```

- **Cipher suites.** Compare the cipher the probe printed with `Get-TlsCipherSuite` on the
  PC. Group Policy (*Computer Configuration → Administrative Templates → Network → SSL
  Configuration Settings*) may have removed every cipher the server offers.

## If it doesn't compile against your version

Every call into the Geo SCADA API is in `GeoScadaSession.cs`. The API changes a little
between versions. For example, older ClearSCADA releases take the password as a `string`
instead of a `SecureString`, and some versions want `Connect(node, ...)` with extra
arguments. If the build fails, compare that file with the *Client API Guide* that ships
with your install (Start menu → Geo SCADA → Documentation).
