using System;
using System.Collections.Generic;
using System.Security;
using ClearScada.Client;
using ClearScada.Client.Advanced;
using Simple = ClearScada.Client.Simple;

namespace GeoScadaClient
{
    /// <summary>
    /// One logged-on connection to a Geo SCADA server, made straight to its IP address
    /// with the .NET Client API (the same protocol ViewX uses, TCP 5481 by default).
    /// All calls into ClearSCADA.Client.dll live in this file, so if your Geo SCADA
    /// version names something differently this is the only place to change.
    /// </summary>
    public sealed class GeoScadaSession : IDisposable
    {
        private readonly Simple.Connection _simple;
        private readonly IServer _advanced;

        public string Host { get; }
        public int Port { get; }

        private GeoScadaSession(string host, int port, Simple.Connection simple, IServer advanced)
        {
            Host = host;
            Port = port;
            _simple = simple;
            _advanced = advanced;
        }

        /// <summary>
        /// Connects and logs on. Each host is tried in turn, so a redundant pair can be
        /// given as "main,standby" and whichever server is reachable is used.
        /// </summary>
        public static GeoScadaSession Open(IReadOnlyList<string> hosts, int port, string user,
                                           SecureString password, string clientName)
        {
            var errors = new List<string>();
            foreach (var host in hosts)
            {
                Simple.Connection simple = null;
                IServer advanced = null;
                try
                {
                    var node = new ServerNode(ConnectionType.Standard, host, port);

                    simple = new Simple.Connection(clientName);
                    simple.Connect(node);
                    simple.LogOn(user, password);

                    // The advanced interface is needed for SQL queries.
                    advanced = node.Connect(clientName, false);
                    advanced.LogOn(user, password);

                    return new GeoScadaSession(host, port, simple, advanced);
                }
                catch (Exception ex)
                {
                    errors.Add($"{host}:{port} - {ex.Message}");
                    advanced?.Dispose();
                    simple?.Dispose();
                }
            }
            throw new InvalidOperationException(
                "Could not log on to any Geo SCADA server:" + Environment.NewLine + "  " +
                string.Join(Environment.NewLine + "  ", errors));
        }

        public object Read(string fullName, string property)
        {
            return GetObject(fullName)[property];
        }

        public void Write(string fullName, string property, object value)
        {
            GetObject(fullName)[property] = value;
        }

        public object Invoke(string fullName, string method, object[] args)
        {
            return GetObject(fullName).InvokeMethod(method, args);
        }

        public QueryTable Query(string sql)
        {
            var result = _advanced.Query(sql, new QueryParams());
            if (result.Status != QueryStatus.Succeeded && result.Status != QueryStatus.NoDataFound)
                throw new InvalidOperationException($"Query failed ({result.Status}): {sql}");

            var table = new QueryTable();
            foreach (var row in result.Rows)
                table.Rows.Add(row.Data);
            return table;
        }

        private Simple.DBObject GetObject(string fullName)
        {
            var obj = _simple.GetObject(fullName);
            if (obj == null)
                throw new ArgumentException($"No database object named '{fullName}'.");
            return obj;
        }

        public void Dispose()
        {
            try { _advanced?.LogOff(); } catch { /* server may already be gone */ }
            _advanced?.Dispose();
            try { _simple?.LogOff(); } catch { }
            _simple?.Dispose();
        }
    }

    public sealed class QueryTable
    {
        public List<object[]> Rows { get; } = new List<object[]>();
    }
}
