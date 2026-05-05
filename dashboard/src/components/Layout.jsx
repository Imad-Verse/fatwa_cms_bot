import React from 'react';
import { NavLink } from 'react-router-dom';
import { 
  LayoutDashboard, 
  FileText, 
  Users, 
  Settings, 
  LogOut, 
  Database,
  BarChart3,
  Search
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const SidebarLink = ({ to, icon: Icon, label }) => (
  <NavLink 
    to={to} 
    className={({ isActive }) => 
      `flex items-center gap-3 px-4 py-3 rounded-xl transition-all ${
        isActive 
        ? 'bg-indigo-500 text-white shadow-lg shadow-indigo-500/30' 
        : 'text-slate-400 hover:bg-white/5 hover:text-white'
      }`
    }
  >
    <Icon size={20} />
    <span className="font-medium">{label}</span>
  </NavLink>
);

const Sidebar = () => {
  const { user, logout } = useAuth();

  return (
    <div className="w-72 border-r border-white/5 flex flex-col p-6 glass h-screen sticky top-0">
      <div className="flex items-center gap-3 px-2 mb-10">
        <div className="w-10 h-10 bg-indigo-500 rounded-xl flex items-center justify-center">
          <Database size={24} className="text-white" />
        </div>
        <span className="text-xl font-bold tracking-tight">Fatwa CMS</span>
      </div>

      <nav className="flex-1 space-y-2">
        <SidebarLink to="/" icon={LayoutDashboard} label="لوحة التحكم" />
        <SidebarLink to="/fatwas" icon={FileText} label="إدارة الفتاوى" />
        <SidebarLink to="/scholars" icon={Users} label="العلماء" />
        <SidebarLink to="/analytics" icon={BarChart3} label="الإحصائيات" />
        <SidebarLink to="/settings" icon={Settings} label="الإعدادات" />
      </nav>

      <div className="mt-auto pt-6 border-t border-white/5">
        <div className="flex items-center gap-3 px-2 mb-6">
          <img 
            src={user?.photo_url || `https://ui-avatars.com/api/?name=${user?.full_name}&background=6366f1&color=fff`} 
            alt="User" 
            className="w-10 h-10 rounded-full border-2 border-indigo-500/20"
          />
          <div className="flex flex-col">
            <span className="text-sm font-semibold truncate max-w-[140px]">{user?.full_name}</span>
            <span className="text-[10px] text-slate-500 uppercase tracking-wider">Admin</span>
          </div>
        </div>

        <button 
          onClick={logout}
          className="flex items-center gap-3 px-4 py-3 w-full rounded-xl text-rose-400 hover:bg-rose-500/10 transition-all"
        >
          <LogOut size={20} />
          <span className="font-medium">تسجيل الخروج</span>
        </button>
      </div>
    </div>
  );
};

export const Layout = ({ children }) => {
  return (
    <div className="flex min-h-screen bg-[#0f172a]">
      <Sidebar />
      <main className="flex-1 p-8">
        <div className="max-w-7xl mx-auto">
          {children}
        </div>
      </main>
    </div>
  );
};
