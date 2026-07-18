import { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";

function Screens() {
  const { session } = useAuth();
  const [showDashboard, setShowDashboard] = useState(false);

  useEffect(() => {
    if (!session) {
      setShowDashboard(false);
      return;
    }
    const id = window.setTimeout(() => setShowDashboard(true), 480);
    return () => window.clearTimeout(id);
  }, [session]);

  if (session && showDashboard) return <Dashboard />;
  return <Login />;
}

function App() {
  return (
    <AuthProvider>
      <Screens />
    </AuthProvider>
  );
}

export default App;