import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { Layout } from './components/Layout';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import FatwasPage from './pages/FatwasPage';
import FatwaEditorPage from './pages/FatwaEditorPage';

// Placeholder pages for routes not yet implemented
const ScholarsPage = () => <div className="fade-in text-right" dir="rtl"><h1 className="text-3xl font-bold">العلماء</h1><p className="text-slate-400 mt-2">قريباً...</p></div>;
const AnalyticsPage = () => <div className="fade-in"><h1 className="text-3xl font-bold">الإحصائيات</h1><p className="text-slate-400 mt-2">قريباً...</p></div>;
const SettingsPage = () => <div className="fade-in"><h1 className="text-3xl font-bold">الإعدادات</h1><p className="text-slate-400 mt-2">قريباً...</p></div>;

const ProtectedRoute = ({ children }) => {
  const { user, loading } = useAuth();

  if (loading) return null; // Or a loading spinner
  
  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return children;
};

const AppRoutes = () => {
  const { user } = useAuth();

  return (
    <Routes>
      <Route 
        path="/login" 
        element={user ? <Navigate to="/" replace /> : <LoginPage />} 
      />
      
      <Route 
        path="/" 
        element={
          <ProtectedRoute>
            <Layout><DashboardPage /></Layout>
          </ProtectedRoute>
        } 
      />

      <Route 
        path="/fatwas" 
        element={
          <ProtectedRoute>
            <Layout><FatwasPage /></Layout>
          </ProtectedRoute>
        } 
      />

      <Route 
        path="/fatwas/new" 
        element={
          <ProtectedRoute>
            <Layout><FatwaEditorPage /></Layout>
          </ProtectedRoute>
        } 
      />

      <Route 
        path="/fatwas/edit/:id" 
        element={
          <ProtectedRoute>
            <Layout><FatwaEditorPage /></Layout>
          </ProtectedRoute>
        } 
      />

      <Route 
        path="/scholars" 
        element={
          <ProtectedRoute>
            <Layout><ScholarsPage /></Layout>
          </ProtectedRoute>
        } 
      />

      <Route 
        path="/analytics" 
        element={
          <ProtectedRoute>
            <Layout><AnalyticsPage /></Layout>
          </ProtectedRoute>
        } 
      />

      <Route 
        path="/settings" 
        element={
          <ProtectedRoute>
            <Layout><SettingsPage /></Layout>
          </ProtectedRoute>
        } 
      />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
};

function App() {
  return (
    <AuthProvider>
      <Router>
        <AppRoutes />
      </Router>
    </AuthProvider>
  );
}

export default App;
