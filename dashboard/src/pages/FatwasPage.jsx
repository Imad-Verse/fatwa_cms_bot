import React, { useState, useEffect } from 'react';
import { 
  Plus, 
  Search, 
  Filter, 
  MoreVertical, 
  Eye, 
  Edit3, 
  Trash2,
  ExternalLink,
  CheckCircle2,
  Clock
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { fatwaService } from '../services/api';
import { motion, AnimatePresence } from 'framer-motion';

const FatwasPage = () => {
  const navigate = useNavigate();
  const [fatwas, setFatwas] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  const fetchFatwas = async () => {
    setLoading(true);
    try {
      const response = await fatwaService.getFatwas({ 
        query: search, 
        page, 
        page_size: 10,
        status: 'all' // Admin view
      });
      setFatwas(response.data.items);
      setTotal(response.data.total);
    } catch (error) {
      console.error('Failed to fetch fatwas:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchFatwas();
  }, [page]);

  const handleSearch = (e) => {
    e.preventDefault();
    setPage(1);
    fetchFatwas();
  };

  return (
    <div className="space-y-8 fade-in">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">إدارة الفتاوى</h1>
          <p className="text-slate-400 mt-1">عرض وتحرير وأرشفة جميع الفتاوى</p>
        </div>
        <button onClick={() => navigate('/fatwas/new')} className="btn btn-primary">
          <Plus size={20} />
          إضافة فتوى جديدة
        </button>
      </div>

      {/* Filters Area */}
      <div className="glass p-4 rounded-2xl flex flex-col md:flex-row gap-4 items-center">
        <form onSubmit={handleSearch} className="relative flex-1">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" size={20} />
          <input 
            type="text" 
            placeholder="بحث في العناوين، المحتوى، أو الرقم..."
            className="w-full bg-white/5 border border-white/10 rounded-xl py-3 pl-12 pr-4 focus:outline-none focus:border-indigo-500 transition-all text-sm"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </form>
        <div className="flex gap-2">
          <button className="btn btn-outline py-2 px-4 text-sm">
            <Filter size={18} />
            تصفية
          </button>
        </div>
      </div>

      {/* Table Area */}
      <div className="glass rounded-[2rem] overflow-hidden border border-white/5">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-white/5 border-b border-white/5">
                <th className="px-6 py-4 text-xs font-bold text-slate-400 uppercase tracking-wider text-right">الفتوى</th>
                <th className="px-6 py-4 text-xs font-bold text-slate-400 uppercase tracking-wider text-right">العالم</th>
                <th className="px-6 py-4 text-xs font-bold text-slate-400 uppercase tracking-wider text-center">الحالة</th>
                <th className="px-6 py-4 text-xs font-bold text-slate-400 uppercase tracking-wider text-center">المشاهدات</th>
                <th className="px-6 py-4 text-xs font-bold text-slate-400 uppercase tracking-wider text-center">الإجراءات</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              <AnimatePresence mode='popLayout'>
              {loading ? (
                <tr>
                  <td colSpan="5" className="px-6 py-12 text-center text-slate-500">جاري التحميل...</td>
                </tr>
              ) : fatwas.length === 0 ? (
                <tr>
                  <td colSpan="5" className="px-6 py-12 text-center text-slate-500">لا توجد نتائج مطابقة</td>
                </tr>
              ) : (
                fatwas.map((fatwa) => (
                  <motion.tr 
                    key={fatwa.id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="hover:bg-white/[0.02] transition-colors group"
                  >
                    <td className="px-6 py-4 text-right">
                      <div className="flex flex-col">
                        <span className="font-bold text-slate-200 line-clamp-1">#{fatwa.fatwa_number} | {fatwa.title}</span>
                        <span className="text-xs text-slate-500 mt-1">{fatwa.categories?.join(' > ') || 'غير مصنف'}</span>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-right">
                      <span className="text-sm text-slate-300">{fatwa.scholar_name}</span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className={`inline-flex items-center gap-1 px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider ${
                        fatwa.status === 'published' 
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                        : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                      }`}>
                        {fatwa.status === 'published' ? <CheckCircle2 size={12} /> : <Clock size={12} />}
                        {fatwa.status === 'published' ? 'منشور' : 'مسودة'}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className="text-sm font-mono text-slate-400">{fatwa.views}</span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <div className="flex items-center justify-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button 
                          onClick={() => navigate(`/fatwas/edit/${fatwa.id}`)}
                          className="p-2 hover:bg-amber-500/10 text-amber-400 rounded-lg transition-colors" 
                          title="تعديل"
                        >
                          <Edit3 size={18} />
                        </button>
                        <button 
                          onClick={async () => {
                            if (window.confirm('هل أنت متأكد من حذف هذه الفتوى نهائياً؟')) {
                              try {
                                await fatwaService.deleteFatwa(fatwa.id);
                                fetchFatwas();
                              } catch (err) {
                                alert('فشل الحذف. قد تكون الدالة غير مفعلة في الـ API.');
                              }
                            }
                          }}
                          className="p-2 hover:bg-rose-500/10 text-rose-400 rounded-lg transition-colors" 
                          title="حذف"
                        >
                          <Trash2 size={18} />
                        </button>
                      </div>
                    </td>
                  </motion.tr>
                ))
              )}
              </AnimatePresence>
            </tbody>
          </table>
        </div>
        
        {/* Pagination */}
        <div className="px-6 py-4 bg-white/5 flex items-center justify-between border-t border-white/5">
          <span className="text-xs text-slate-500 font-medium">
            عرض {fatwas.length} من أصل {total} فتوى
          </span>
          <div className="flex gap-2">
            <button 
              disabled={page === 1}
              onClick={() => setPage(p => p - 1)}
              className="btn btn-outline py-1.5 px-3 text-xs disabled:opacity-30"
            >
              السابق
            </button>
            <button 
              disabled={page * 10 >= total}
              onClick={() => setPage(p => p + 1)}
              className="btn btn-outline py-1.5 px-3 text-xs disabled:opacity-30"
            >
              التالي
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default FatwasPage;
