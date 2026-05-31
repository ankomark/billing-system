import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import AuthLayout from "../layouts/AuthLayout";
import { login, getUser, isAuthenticated } from "../services/auth";
import api from "../services/api";

export default function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) return;
    const user = getUser();
    if (user?.role === "customer") navigate("/customer/pppoe", { replace: true });
    else if (user) navigate("/admin/dashboard", { replace: true });
    else localStorage.clear();
  }, [navigate]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const data = await login(username, password);
      localStorage.setItem("access_token", data.access);
      localStorage.setItem("refresh_token", data.refresh);
      const profileRes = await api.get("auth/profile/");
      localStorage.setItem("user", JSON.stringify(profileRes.data));
      if (profileRes.data.role === "customer") {
        navigate("/customer/pppoe", { replace: true });
      } else {
        navigate("/admin/dashboard", { replace: true });
      }
    } catch {
      setError("Invalid username or password. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout>
      <div className="mb-6">
        <h2 className="text-xl font-bold text-slate-800">Welcome back</h2>
        <p className="text-slate-500 text-sm mt-1">Sign in to your account to continue</p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-5 text-sm">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">
            Username
          </label>
          <input
            type="text"
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
            placeholder="Enter your username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoFocus
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">
            Password
          </label>
          <input
            type="password"
            className="w-full border border-slate-300 rounded-lg px-4 py-2.5 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
            placeholder="Enter your password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full bg-blue-600 hover:bg-blue-700 text-white py-2.5 rounded-lg font-semibold text-sm disabled:opacity-50 disabled:cursor-not-allowed transition-colors mt-2"
        >
          {loading ? "Signing in…" : "Sign In"}
        </button>
      </form>
    </AuthLayout>
  );
}
