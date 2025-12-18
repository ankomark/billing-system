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

  // ✅ Redirect already-authenticated users
 useEffect(() => {
  if (isAuthenticated()) {
    const user = getUser();
    console.log("User in useEffect:", user); // Debugging
    
    // More defensive check
    if (user && user.role === "customer") {
      navigate("/customer/pppoe", { replace: true });
    } else if (user && (user.role === "admin" || user.role === "staff" || user.role === "superadmin")) {
      navigate("/admin/dashboard", { replace: true });
    } else {
      // If we can't determine role, redirect to login
      localStorage.clear();
      navigate("/login", { replace: true });
    }
  }
}, [navigate]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      // 1️⃣ Login
      const data = await login(username, password);

      localStorage.setItem("access_token", data.access);
      localStorage.setItem("refresh_token", data.refresh);

      // 2️⃣ Fetch profile
      const profileRes = await api.get("auth/profile/");
      localStorage.setItem("user", JSON.stringify(profileRes.data));

      // 3️⃣ Redirect by role
      if (profileRes.data.role === "customer") {
        navigate("/customer/pppoe", { replace: true });
      } else {
        navigate("/admin/dashboard", { replace: true });
      }
    } catch {
      setError("Invalid username or password");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout>
      <h2 className="text-2xl font-bold text-center mb-6">Login</h2>

      {error && (
        <div className="bg-red-100 text-red-700 p-3 rounded mb-4 text-sm">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <input
          type="text"
          placeholder="Username"
          className="w-full border p-3 rounded"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />

        <input
          type="password"
          placeholder="Password"
          className="w-full border p-3 rounded"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />

        <button
          type="submit"
          disabled={loading}
          className="w-full bg-blue-600 text-white py-3 rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? "Logging in..." : "Login"}
        </button>
      </form>
    </AuthLayout>
  );
}
