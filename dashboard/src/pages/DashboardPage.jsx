import React, { useState, useEffect } from 'react';
import { 
  FileText, 
  Users, 
  Eye, 
  Heart,
  TrendingUp,
  Clock,
  CheckCircle2
} from 'lucide-react';
import { fatwaService } from '../services/api';
import { motion } from 'framer-motion';

const StatCard = ({ icon: Icon, label, value, trend, color }) => (
  <div className="glass-card flex flex-col gap-4">
    <div className="flex items-center justify-between">
      <div className={`p-3 rounded-2xl ${color} bg-opacity-10`}>
        <Icon className={color.replace('bg-', 'text-')} size={24} />
      </div>
      {trend && (
        <span className="text-xs font-medium text-emerald-400 bg-emerald-400/10 px-2 py-1 rounded-full">
          +{trend}%
        </span>
      )}
    </div>
    <div>
      <p className="text-slate-400 text-sm font-medium">{label}</p>
      <h3 className="text-2xl font-bold mt-1">{value}</h3>
    </div>
  </div>
);

const DashboardPage = () => {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const response = await fatwaService.getStats();
        setStats(response.data);
      } catch (error) {
        console.error('Failed to fetch stats:', error);
      } finally {
        setLoading(false);
      }
    };
    fetchStats();
  }, []);

  if (loading) return (
    <div className="flex items-center justify-center h-[60vh]">
      <div className="w-12 h-12 border-4 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin"></div>
    </div>
  );

  return (
    <div className="space-y-8 fade-in">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">نظرة عامة</h1>
        <p className="text-slate-400 mt-1">مرحباً بك في لوحة تحكم Fatwa CMS</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard 
          icon={FileText} 
          label="إجمالي الفتاوى" 
          value={stats?.total_fatwas || 0} 
          color="bg-indigo-500" 
        />
        <StatCard 
          icon={CheckCircle2} 
          label="الفتاوى المنشورة" 
          value={stats?.published_fatwas || 0} 
          color="bg-emerald-500" 
        />
        <StatCard 
          icon={Eye} 
          label="إجمالي المشاهدات" 
          value={stats?.total_views || 0} 
          color="bg-blue-500" 
        />
        <StatCard 
          icon={Heart} 
          label="إجمالي التفضيلات" 
          value={stats?.favorites_count || 0} 
          color="bg-rose-500" 
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 glass-card">
          <h3 className="text-lg font-bold mb-6 flex items-center gap-2">
            <TrendingUp size={20} className="text-indigo-400" />
            نشاط النظام
          </h3>
          <div className="space-y-6">
             <div className="flex items-center justify-between p-4 bg-white/5 rounded-2xl border border-white/5">
                <div className="flex items-center gap-4">
                   <div className="w-10 h-10 bg-blue-500/10 rounded-xl flex items-center justify-center">
                      <Users className="text-blue-400" size={20} />
                   </div>
                   <div>
                      <p className="font-semibold">المشتركين النشطين</p>
                      <p className="text-xs text-slate-500">إجمالي مستخدمي البوت</p>
                   </div>
                </div>
                <span className="text-xl font-bold">{stats?.total_users || 0}</span>
             </div>
             
             <div className="flex items-center justify-between p-4 bg-white/5 rounded-2xl border border-white/5">
                <div className="flex items-center gap-4">
                   <div className="w-10 h-10 bg-amber-500/10 rounded-xl flex items-center justify-center">
                      <FileText className="text-amber-400" size={20} />
                   </div>
                   <div>
                      <p className="font-semibold">المواضيع الفقهية</p>
                      <p className="text-xs text-slate-500">تصنيفات وموضوعات مؤرشفة</p>
                   </div>
                </div>
                <span className="text-xl font-bold">{stats?.topics || 0}</span>
             </div>
          </div>
        </div>

        <div className="glass-card">
          <h3 className="text-lg font-bold mb-6 flex items-center gap-2">
            <Clock size={20} className="text-indigo-400" />
            حالة النظام
          </h3>
          <div className="space-y-4">
             <div className="flex justify-between text-sm py-2 border-b border-white/5">
                <span className="text-slate-400">مدة التشغيل</span>
                <span className="text-emerald-400 font-medium">{stats?.uptime || 'N/A'}</span>
             </div>
             <div className="flex justify-between text-sm py-2 border-b border-white/5">
                <span className="text-slate-400">وضع الصيانة</span>
                <span className={stats?.maintenance_mode === "1" ? "text-rose-400" : "text-emerald-400"}>
                  {stats?.maintenance_mode === "1" ? "نشط" : "غير نشط"}
                </span>
             </div>
             <div className="flex justify-between text-sm py-2">
                <span className="text-slate-400">إصدار النظام</span>
                <span className="font-mono text-xs">v1.0.0-stable</span>
             </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DashboardPage;
