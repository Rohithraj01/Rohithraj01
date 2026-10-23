using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security;
using System.Threading;

namespace GeoScadaClient
{
    /// <summary>
    /// geoscada - talk to a Geo SCADA server by IP instead of opening a Remote Desktop
    /// session on it. Run without arguments for help.
    /// </summary>
    internal static class Program
    {
        private const string Usage = @"geoscada - Geo SCADA by IP, no Remote Desktop needed

Usage:
  geoscada [connection options] <command> [arguments]

Connection options (or the environment variables in brackets):
  --host <ip[,ip]>     server IP / name; give main,standby for a redundant pair [GEOSCADA_HOST]
  --port <n>           client port, default 5481                               [GEOSCADA_PORT]
  --user <name>        Geo SCADA user (not a Windows account)                  [GEOSCADA_USER]
                       password: [GEOSCADA_PASSWORD], otherwise you are asked for it
  --allow-write        needed for 'write' and 'call', which change the live system

Commands:
  status                              log on and show which server answered
  browse [group]                      list what is inside a group (default: the root)
  read <object> [property]            read a property, default CurrentValue
  watch <object> [property] [secs]    re-read every few seconds (default 2) until Ctrl+C
  write <object> <property> <value>   set a property (e.g. CurrentValue of an internal point)
  call <object> <method> [args...]    run a database method (e.g. Override, Control)
  query ""<sql>""                       run an SQL query, e.g.
                                      ""SELECT FullName, CurrentValue FROM CDBPoint""
  shell                               stay logged on and type the commands above

Examples:
  geoscada --host 10.20.0.15 --user engineer status
  geoscada --host 10.20.0.15,10.20.0.16 --user engineer read ""Site A.Pump 1.Run Status""
  geoscada --host 10.20.0.15 --user engineer --allow-write write ""Site A.Setpoint"" CurrentValue 42.5
";

        private static int Main(string[] args)
        {
            Options opts;
            try
            {
                opts = Options.Parse(args);
            }
            catch (ArgumentException ex)
            {
                Console.Error.WriteLine(ex.Message);
                Console.Error.WriteLine();
                Console.Error.Write(Usage);
                return 2;
            }

            if (opts.Command.Count == 0 || opts.Command[0] is "help" or "-h" or "--help")
            {
                Console.Write(Usage);
                return 0;
            }

            try
            {
                using (var password = ReadPassword(opts.User))
                using (var session = GeoScadaSession.Open(opts.Hosts, opts.Port, opts.User, password,
                                                          "geoscada-cli"))
                {
                    if (opts.Command[0] == "shell")
                        return RunShell(session, opts);
                    return Run(session, opts, opts.Command);
                }
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("Error: " + ex.Message);
                return 1;
            }
        }

        private static int Run(GeoScadaSession session, Options opts, IReadOnlyList<string> cmd)
        {
            string Arg(int i, string fallback = null) =>
                i < cmd.Count ? cmd[i] : fallback ?? throw new ArgumentException(
                    $"'{cmd[0]}' is missing an argument. Run 'geoscada help'.");

            switch (cmd[0].ToLowerInvariant())
            {
                case "status":
                    Console.WriteLine($"Logged on to {session.Host}:{session.Port} as {opts.User}.");
                    return 0;

                case "browse":
                    Browse(session, Arg(1, ""));
                    return 0;

                case "read":
                    Console.WriteLine(Format(session.Read(Arg(1), Arg(2, "CurrentValue"))));
                    return 0;

                case "watch":
                {
                    var name = Arg(1);
                    var property = Arg(2, "CurrentValue");
                    var seconds = double.Parse(Arg(3, "2"), CultureInfo.InvariantCulture);
                    using (var stop = new ManualResetEventSlim())
                    {
                        Console.CancelKeyPress += (_, e) => { e.Cancel = true; stop.Set(); };
                        do
                        {
                            Console.WriteLine($"{DateTime.Now:HH:mm:ss}  {Format(session.Read(name, property))}");
                        } while (!stop.Wait(TimeSpan.FromSeconds(seconds)));
                    }
                    return 0;
                }

                case "write":
                    RequireWrite(opts);
                    session.Write(Arg(1), Arg(2), ParseValue(Arg(3)));
                    Console.WriteLine("OK");
                    return 0;

                case "call":
                {
                    RequireWrite(opts);
                    var methodArgs = cmd.Skip(3).Select(ParseValue).ToArray();
                    var result = session.Invoke(Arg(1), Arg(2), methodArgs);
                    Console.WriteLine(result == null ? "OK" : Format(result));
                    return 0;
                }

                case "query":
                    PrintRows(session.Query(Arg(1)));
                    return 0;

                default:
                    throw new ArgumentException($"Unknown command '{cmd[0]}'. Run 'geoscada help'.");
            }
        }

        private static int RunShell(GeoScadaSession session, Options opts)
        {
            Console.WriteLine($"Logged on to {session.Host}:{session.Port}. Type 'help' for commands, 'exit' to quit.");
            string line;
            while (Prompt() && (line = Console.ReadLine()) != null)
            {
                var cmd = Options.SplitCommandLine(line);
                if (cmd.Count == 0) continue;
                if (cmd[0] is "exit" or "quit") break;
                if (cmd[0] is "help" or "shell") { Console.Write(Usage); continue; }
                try { Run(session, opts, cmd); }
                catch (Exception ex) { Console.Error.WriteLine("Error: " + ex.Message); }
            }
            return 0;

            bool Prompt() { Console.Write("geoscada> "); return true; }
        }

        private static void Browse(GeoScadaSession session, string group)
        {
            // Direct children only: "Group.Name" but not "Group.Name.Deeper".
            string sql = group.Length == 0
                ? "SELECT FullName, TypeDesc FROM CDBObject WHERE FullName NOT LIKE '%.%' ORDER BY FullName"
                : $"SELECT FullName, TypeDesc FROM CDBObject WHERE FullName LIKE '{Sql(group)}.%' " +
                  $"AND FullName NOT LIKE '{Sql(group)}.%.%' ORDER BY FullName";
            PrintRows(session.Query(sql));
        }

        private static void PrintRows(QueryTable table)
        {
            if (table.Rows.Count == 0)
            {
                Console.WriteLine("(no rows)");
                return;
            }
            foreach (var row in table.Rows)
                Console.WriteLine(string.Join("\t", row.Select(Format)));
        }

        private static void RequireWrite(Options opts)
        {
            if (!opts.AllowWrite)
                throw new InvalidOperationException(
                    "This changes the live system. Add --allow-write to the command to confirm.");
        }

        private static SecureString ReadPassword(string user)
        {
            var secure = new SecureString();
            var fromEnv = Environment.GetEnvironmentVariable("GEOSCADA_PASSWORD");
            if (fromEnv != null)
            {
                foreach (var c in fromEnv) secure.AppendChar(c);
            }
            else
            {
                Console.Error.Write($"Password for {user}: ");
                ConsoleKeyInfo key;
                while ((key = Console.ReadKey(intercept: true)).Key != ConsoleKey.Enter)
                {
                    if (key.Key == ConsoleKey.Backspace)
                    {
                        if (secure.Length > 0) secure.RemoveAt(secure.Length - 1);
                    }
                    else if (!char.IsControl(key.KeyChar))
                    {
                        secure.AppendChar(key.KeyChar);
                    }
                }
                Console.Error.WriteLine();
            }
            secure.MakeReadOnly();
            return secure;
        }

        /// <summary>Numbers and true/false become typed values; anything else stays text.</summary>
        private static object ParseValue(string text)
        {
            if (bool.TryParse(text, out var b)) return b;
            if (long.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out var l)) return l;
            if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var d)) return d;
            return text;
        }

        private static string Format(object value) => value switch
        {
            null => "(null)",
            DateTime t => t.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture),
            IFormattable f => f.ToString(null, CultureInfo.InvariantCulture),
            _ => value.ToString(),
        };

        private static string Sql(string text) => text.Replace("'", "''");
    }

    internal sealed class Options
    {
        public List<string> Hosts { get; private set; } = new List<string>();
        public int Port { get; private set; } = 5481;
        public string User { get; private set; }
        public bool AllowWrite { get; private set; }
        public List<string> Command { get; } = new List<string>();

        public static Options Parse(string[] args)
        {
            var o = new Options();
            string host = Environment.GetEnvironmentVariable("GEOSCADA_HOST");
            string port = Environment.GetEnvironmentVariable("GEOSCADA_PORT");
            o.User = Environment.GetEnvironmentVariable("GEOSCADA_USER");

            for (int i = 0; i < args.Length; i++)
            {
                string Next() => i + 1 < args.Length
                    ? args[++i]
                    : throw new ArgumentException($"{args[i]} needs a value.");

                switch (args[i])
                {
                    case "--host": host = Next(); break;
                    case "--port": port = Next(); break;
                    case "--user": o.User = Next(); break;
                    case "--allow-write": o.AllowWrite = true; break;
                    default: o.Command.Add(args[i]); break;
                }
            }

            bool helpOnly = o.Command.Count == 0 || o.Command[0] is "help" or "-h" or "--help";
            if (helpOnly) return o;

            if (string.IsNullOrWhiteSpace(host))
                throw new ArgumentException("No server given: use --host <ip> or set GEOSCADA_HOST.");
            if (string.IsNullOrWhiteSpace(o.User))
                throw new ArgumentException("No user given: use --user <name> or set GEOSCADA_USER.");

            o.Hosts = host.Split(',').Select(h => h.Trim()).Where(h => h.Length > 0).ToList();
            if (port != null && (!int.TryParse(port, out var p) || p <= 0 || p > 65535))
                throw new ArgumentException($"'{port}' is not a valid port.");
            if (port != null) o.Port = int.Parse(port);
            return o;
        }

        /// <summary>Splits a shell line on spaces, keeping "quoted text" together.</summary>
        public static List<string> SplitCommandLine(string line)
        {
            var parts = new List<string>();
            var current = new System.Text.StringBuilder();
            bool quoted = false, any = false;
            foreach (var c in line)
            {
                if (c == '"') { quoted = !quoted; any = true; }
                else if (char.IsWhiteSpace(c) && !quoted)
                {
                    if (any) { parts.Add(current.ToString()); current.Clear(); any = false; }
                }
                else { current.Append(c); any = true; }
            }
            if (any) parts.Add(current.ToString());
            return parts;
        }
    }
}
