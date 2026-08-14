import { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { Dashboard } from "./pages/Dashboard";

function Screens() {
  const { session } = useAuth();
  const [showDashboard, setShowDashboard] = useState(false);
  const [authView, setAuthView] = useState<"login" | "register">("login");

  useEffect(() => {
    if (!session) {
      setShowDashboard(false);
      return;
    }
    const id = window.setTimeout(() => setShowDashboard(true), 480);
    return () => window.clearTimeout(id);
  }, [session]);

  if (session && showDashboard) return <Dashboard />;

  if (authView === "register") {
    return <Register onLoginClick={() => setAuthView("login")} />;
  }

  return <Login onRegisterClick={() => setAuthView("register")} />;
}

function App() {
  return (
    <AuthProvider>
      <Screens />
    </AuthProvider>
  );
}

export default App;