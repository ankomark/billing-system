import { useEffect, useState } from "react";
import { fetchAdminPPPoESessions } from "../../services/pppoe";
import api from "../../services/api"; // Make sure this is imported

function formatMB(bytes) {
  return (bytes / (1024 * 1024)).toFixed(2) + " MB";
}

function formatUptime(seconds) {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

export default function PPPoESessions() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [disconnecting, setDisconnecting] = useState({});

  useEffect(() => {
    const loadSessions = async () => {
      try {
        setLoading(true);
        const data = await fetchAdminPPPoESessions();
        setSessions(data);
      } catch (error) {
        console.error("Failed to fetch sessions:", error);
      } finally {
        setLoading(false);
      }
    };

    loadSessions();
    
    const interval = setInterval(loadSessions, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleDisconnect = async (username) => {
    if (!window.confirm(`Are you sure you want to disconnect ${username}?`)) {
      return;
    }

    try {
      setDisconnecting(prev => ({ ...prev, [username]: true }));
      
      await api.post("/admin/pppoe/disconnect/", { username });
      
      // Refresh sessions after successful disconnect
      const updatedSessions = await fetchAdminPPPoESessions();
      setSessions(updatedSessions);
      
      // Show success message
      alert(`Successfully disconnected ${username}`);
    } catch (error) {
      console.error("Disconnect failed:", error);
      alert(`Failed to disconnect ${username}: ${error.message}`);
    } finally {
      setDisconnecting(prev => ({ ...prev, [username]: false }));
    }
  };

  const getRouterColor = (router) => {
    const colors = {
      'main-router': 'bg-blue-100 text-blue-800',
      'backup-router': 'bg-yellow-100 text-yellow-800',
      'default': 'bg-gray-100 text-gray-800'
    };
    return colors[router] || colors.default;
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-200 to-orange-300 p-4 md:p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <h1 className="text-3xl md:text-4xl font-bold text-gray-900">
                PPPoE Sessions
              </h1>
              <p className="text-gray-600 mt-2">
                Real-time monitoring of active connections
              </p>
            </div>
            
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
                <span className="text-sm font-medium text-gray-700">
                  {sessions.length} Active Sessions
                </span>
              </div>
              <div className="hidden md:block text-sm text-gray-500">
                Auto-refresh every 10s
              </div>
            </div>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <div className="bg-white rounded-xl shadow-sm p-5 border border-gray-200">
            <div className="text-gray-500 text-sm font-medium">Total Sessions</div>
            <div className="text-3xl font-bold text-gray-900 mt-2">{sessions.length}</div>
          </div>
          
          <div className="bg-white rounded-xl shadow-sm p-5 border border-gray-200">
            <div className="text-gray-500 text-sm font-medium">Total Bandwidth</div>
            <div className="text-3xl font-bold text-gray-900 mt-2">
              {formatMB(sessions.reduce((sum, s) => sum + s.rx_bytes + s.tx_bytes, 0))}
            </div>
          </div>
          
          <div className="bg-white rounded-xl shadow-sm p-5 border border-gray-200">
            <div className="text-gray-500 text-sm font-medium">Avg Uptime</div>
            <div className="text-3xl font-bold text-gray-900 mt-2">
              {sessions.length > 0 
                ? formatUptime(sessions.reduce((sum, s) => sum + (parseInt(s.uptime) || 0), 0) / sessions.length)
                : '0m'
              }
            </div>
          </div>
          
          <div className="bg-white rounded-xl shadow-sm p-5 border border-gray-200">
            <div className="text-gray-500 text-sm font-medium">Active Routers</div>
            <div className="text-3xl font-bold text-gray-900 mt-2">
              {[...new Set(sessions.map(s => s.router))].length}
            </div>
          </div>
        </div>

        {/* Sessions Table */}
        <div className="bg-white rounded-2xl shadow-lg overflow-hidden border border-gray-200">
          <div className="px-6 py-4 border-b border-gray-200 bg-gradient-to-r from-gray-50 to-white">
            <h2 className="text-xl font-semibold text-gray-800">Live Sessions</h2>
            <p className="text-gray-600 text-sm mt-1">
              Click on any session for detailed information
            </p>
          </div>

          {loading ? (
            <div className="p-12 text-center">
              <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
              <p className="mt-4 text-gray-600">Loading active sessions...</p>
            </div>
          ) : sessions.length === 0 ? (
            <div className="p-12 text-center">
              <div className="text-gray-400 text-6xl mb-4">📡</div>
              <h3 className="text-xl font-medium text-gray-700">No Active Sessions</h3>
              <p className="text-gray-500 mt-2">Waiting for PPPoE connections...</p>
            </div>
          ) : (
            <>
              {/* Desktop Table */}
              <div className="hidden lg:block overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="bg-gray-50">
                      <th className="py-4 px-6 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                        Customer
                      </th>
                      <th className="py-4 px-6 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                        Username
                      </th>
                      <th className="py-4 px-6 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                        IP Address
                      </th>
                      <th className="py-4 px-6 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                        Uptime
                      </th>
                      <th className="py-4 px-6 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                        Download
                      </th>
                      <th className="py-4 px-6 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                        Upload
                      </th>
                      <th className="py-4 px-6 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                        Router
                      </th>
                      <th className="py-4 px-6 text-left text-xs font-semibold text-gray-700 uppercase tracking-wider">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {sessions.map((session, index) => (
                      <tr 
                        key={index} 
                        className="hover:bg-blue-50 transition-colors duration-150"
                      >
                        <td className="py-4 px-6 whitespace-nowrap">
                          <div className="flex items-center">
                            <div className="flex-shrink-0 h-10 w-10 bg-gradient-to-r from-blue-500 to-blue-600 rounded-lg flex items-center justify-center">
                              <span className="text-white font-bold">
                                {session.customer?.charAt(0) || 'C'}
                              </span>
                            </div>
                            <div className="ml-4">
                              <div className="text-sm font-medium text-gray-900">
                                {session.customer || 'Unknown Customer'}
                              </div>
                              <div className="text-sm text-gray-500">
                                ID: {session.id || 'N/A'}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="py-4 px-6 whitespace-nowrap">
                          <div className="text-sm font-mono text-gray-900 bg-gray-50 px-3 py-1 rounded-md inline-block">
                            {session.username}
                          </div>
                        </td>
                        <td className="py-4 px-6 whitespace-nowrap">
                          <div className="text-sm font-medium text-gray-900">
                            {session.ip_address}
                          </div>
                        </td>
                        <td className="py-4 px-6 whitespace-nowrap">
                          <div className="text-sm text-gray-900 font-medium">
                            {formatUptime(parseInt(session.uptime) || 0)}
                          </div>
                          <div className="text-xs text-gray-500">
                            {session.uptime} seconds
                          </div>
                        </td>
                        <td className="py-4 px-6 whitespace-nowrap">
                          <div className="text-sm font-medium text-blue-700">
                            {formatMB(session.rx_bytes)}
                          </div>
                          <div className="text-xs text-gray-500">
                            Received
                          </div>
                        </td>
                        <td className="py-4 px-6 whitespace-nowrap">
                          <div className="text-sm font-medium text-green-700">
                            {formatMB(session.tx_bytes)}
                          </div>
                          <div className="text-xs text-gray-500">
                            Sent
                          </div>
                        </td>
                        <td className="py-4 px-6 whitespace-nowrap">
                          <span className={`px-3 py-1 rounded-full text-xs font-medium ${getRouterColor(session.router)}`}>
                            {session.router}
                          </span>
                        </td>
                        <td className="py-4 px-6 whitespace-nowrap">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDisconnect(session.username);
                            }}
                            disabled={disconnecting[session.username]}
                            className={`bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded text-xs font-semibold transition-colors ${
                              disconnecting[session.username] ? 'opacity-50 cursor-not-allowed' : ''
                            }`}
                          >
                            {disconnecting[session.username] ? 'Disconnecting...' : 'Disconnect'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Mobile Cards */}
              <div className="lg:hidden divide-y divide-gray-200">
                {sessions.map((session, index) => (
                  <div 
                    key={index} 
                    className="p-4 hover:bg-gray-50 transition-colors duration-150"
                  >
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex items-center">
                        <div className="flex-shrink-0 h-12 w-12 bg-gradient-to-r from-blue-500 to-blue-600 rounded-lg flex items-center justify-center">
                          <span className="text-white font-bold text-lg">
                            {session.customer?.charAt(0) || 'C'}
                          </span>
                        </div>
                        <div className="ml-4">
                          <div className="text-base font-semibold text-gray-900">
                            {session.customer || 'Unknown Customer'}
                          </div>
                          <div className="text-sm text-gray-600 font-mono">
                            {session.username}
                          </div>
                        </div>
                      </div>
                      <span className={`px-3 py-1 rounded-full text-xs font-medium ${getRouterColor(session.router)}`}>
                        {session.router}
                      </span>
                    </div>
                    
                    <div className="grid grid-cols-2 gap-4 mt-4">
                      <div>
                        <div className="text-xs text-gray-500">IP Address</div>
                        <div className="text-sm font-medium text-gray-900">{session.ip_address}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Uptime</div>
                        <div className="text-sm font-medium text-gray-900">
                          {formatUptime(parseInt(session.uptime) || 0)}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Download</div>
                        <div className="text-sm font-medium text-blue-700">
                          {formatMB(session.rx_bytes)}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500">Upload</div>
                        <div className="text-sm font-medium text-green-700">
                          {formatMB(session.tx_bytes)}
                        </div>
                      </div>
                    </div>
                    
                    {/* ADDED DISCONNECT BUTTON FOR MOBILE */}
                    <div className="mt-4 pt-4 border-t border-gray-100">
                      <button
                        onClick={() => handleDisconnect(session.username)}
                        disabled={disconnecting[session.username]}
                        className={`w-full bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded text-sm font-medium transition-colors ${
                          disconnecting[session.username] ? 'opacity-50 cursor-not-allowed' : ''
                        }`}
                      >
                        {disconnecting[session.username] ? 'Disconnecting...' : 'Disconnect Session'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Footer */}
          <div className="px-6 py-4 border-t border-gray-200 bg-gray-50 flex flex-col sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm text-gray-600 mb-2 sm:mb-0">
              Showing <span className="font-medium">{sessions.length}</span> active sessions
            </div>
            <div className="flex items-center text-sm text-gray-500">
              <div className="w-2 h-2 bg-green-500 rounded-full mr-2 animate-pulse"></div>
              Live • Last updated just now
            </div>
          </div>
        </div>

        {/* Legend */}
        <div className="mt-6 bg-white rounded-xl shadow-sm p-4 border border-gray-200">
          <div className="flex flex-wrap gap-4 items-center">
            <div className="text-sm font-medium text-gray-700">Legend:</div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 bg-blue-100 border border-blue-300 rounded"></div>
              <span className="text-sm text-gray-600">Main Router</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 bg-yellow-100 border border-yellow-300 rounded"></div>
              <span className="text-sm text-gray-600">Backup Router</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 bg-green-500 rounded-full animate-pulse"></div>
              <span className="text-sm text-gray-600">Active Connection</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 bg-red-500 rounded"></div>
              <span className="text-sm text-gray-600">Disconnect Action</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}