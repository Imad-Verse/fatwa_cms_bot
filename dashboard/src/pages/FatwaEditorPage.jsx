import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { 
  Save, 
  X, 
  FileText, 
  User, 
  BookOpen, 
  Link as LinkIcon, 
  Music,
  CheckCircle2,
  ChevronRight,
  AlertCircle,
  Sparkles
} from 'lucide-react';
import { fatwaService } from '../services/api';
import { motion } from 'framer-motion';

const FatwaEditorPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [loading, setLoading] = useState(isEdit);
  const [saving, setSaving] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [error, setError] = useState(null);

  // Form State
  const [formData, setFormData] = useState({
    title: '',
    scholar_name: '',
    question: '',
    answer: '',
    source_name: '',
    source_title: '',
    source_url: '',
    audio_url: '',
    status: 'published',
    classifications: [] // [{category_id, topic_ids: [], slot_index: 1}]
  });

  // Lookups
  const [scholars, setScholars] = useState([]);
  const [categories, setCategories] = useState([]);
  // (Future: Topics based on category)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [scholarsRes, catsRes] = await Promise.all([
          fatwaService.getScholars(),
          fatwaService.getCategories()
        ]);
        setScholars(scholarsRes.data.items);
        setCategories(catsRes.data.items);

        if (isEdit) {
          const fatwaRes = await fatwaService.getFatwa(id);
          const f = fatwaRes.data;
          setFormData({
            title: f.title,
            scholar_name: f.scholar_name,
            question: f.question,
            answer: f.answer,
            source_name: f.source_name,
            source_title: f.source_title,
            source_url: f.source_link || '',
            audio_url: f.audio_link || '',
            status: f.status,
            classifications: f.classifications || []
          });
        }
      } catch (err) {
        setError('تعذر تحميل البيانات. يرجى المحاولة لاحقاً.');
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [id, isEdit]);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);

    try {
      if (isEdit) {
        await fatwaService.updateFatwa(id, formData);
      } else {
        await fatwaService.createFatwa(formData);
      }
      navigate('/fatwas');
    } catch (err) {
      setError(err.response?.data?.detail || 'حدث خطأ أثناء الحفظ.');
    } finally {
      setSaving(false);
    }
  };

  const handleSuggestTags = async () => {
    if (!formData.question && !formData.answer) {
      alert('يرجى كتابة نص السؤال أو الجواب أولاً.');
      return;
    }
    setSuggesting(true);
    try {
      const response = await fatwaService.suggestTags(formData.question + ' ' + formData.answer);
      if (response.data.suggestions) {
        const suggestions = response.data.suggestions.join(', ');
        alert('المصطلحات المقترحة: ' + suggestions);
        if (!formData.title) {
          setFormData(prev => ({ ...prev, title: response.data.suggestions[0] }));
        }
      }
    } catch (err) {
      console.error(err);
      alert('فشل في تحليل النص بالذكاء الاصطناعي.');
    } finally {
      setSuggesting(false);
    }
  };

  if (loading) return <div className="flex justify-center p-20"><div className="w-10 h-10 border-4 border-indigo-500/20 border-t-indigo-500 rounded-full animate-spin"></div></div>;

  return (
    <div className="space-y-8 fade-in text-right" dir="rtl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            {isEdit ? 'تعديل الفتوى' : 'إضافة فتوى جديدة'}
          </h1>
          <div className="flex items-center gap-2 text-slate-400 mt-2 text-sm">
             <span>إدارة الفتاوى</span>
             <ChevronRight size={14} />
             <span className="text-slate-200">{isEdit ? 'تعديل' : 'إضافة'}</span>
          </div>
        </div>
        <div className="flex gap-3">
          <button onClick={() => navigate('/fatwas')} className="btn btn-outline">
            <X size={18} />
            إلغاء
          </button>
          <button onClick={handleSubmit} disabled={saving} className="btn btn-primary">
            {saving ? <div className="w-5 h-5 border-2 border-white/20 border-t-white rounded-full animate-spin"></div> : <Save size={18} />}
            {isEdit ? 'حفظ التغييرات' : 'حفظ الفتوى'}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-rose-500/10 border border-rose-500/20 p-4 rounded-2xl flex items-center gap-3 text-rose-400">
          <AlertCircle size={20} />
          <p className="text-sm font-medium">{error}</p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Main Content Area */}
        <div className="lg:col-span-2 space-y-6">
          <div className="glass-card space-y-6">
            <div className="flex items-center gap-2 text-indigo-400 font-bold mb-2">
              <FileText size={20} />
              <h3>المحتوى الأساسي</h3>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-sm text-slate-400 block">عنوان الفتوى</label>
                <button 
                  type="button"
                  onClick={handleSuggestTags}
                  disabled={suggesting}
                  className="text-xs flex items-center gap-1 text-indigo-400 hover:text-indigo-300 transition-colors"
                >
                  <Sparkles size={12} className={suggesting ? 'animate-spin' : ''} />
                  {suggesting ? 'جاري التحليل...' : 'اقتراح بواسطة AI'}
                </button>
              </div>
              <input 
                type="text" 
                name="title"
                value={formData.title}
                onChange={handleChange}
                placeholder="أدخل عنواناً معبراً ومختصراً..."
                className="w-full bg-white/5 border border-white/10 rounded-xl py-3 px-4 focus:outline-none focus:border-indigo-500 transition-all"
                required
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm text-slate-400 block">نص السؤال</label>
              <textarea 
                name="question"
                value={formData.question}
                onChange={handleChange}
                rows="4"
                placeholder="اكتب السؤال كما ورد..."
                className="w-full bg-white/5 border border-white/10 rounded-xl py-3 px-4 focus:outline-none focus:border-indigo-500 transition-all resize-none"
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm text-slate-400 block">نص الجواب</label>
              <textarea 
                name="answer"
                value={formData.answer}
                onChange={handleChange}
                rows="10"
                placeholder="اكتب الجواب بالتفصيل..."
                className="w-full bg-white/5 border border-white/10 rounded-xl py-3 px-4 focus:outline-none focus:border-indigo-500 transition-all resize-none"
                required
              />
            </div>
          </div>

          <div className="glass-card space-y-6">
            <div className="flex items-center gap-2 text-indigo-400 font-bold mb-2">
              <LinkIcon size={20} />
              <h3>المصادر والوسائط</h3>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <label className="text-sm text-slate-400 block">اسم المصدر (كتاب/موقع)</label>
                <input 
                  type="text" 
                  name="source_name"
                  value={formData.source_name}
                  onChange={handleChange}
                  className="w-full bg-white/5 border border-white/10 rounded-xl py-3 px-4 focus:outline-none focus:border-indigo-500 transition-all"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm text-slate-400 block">عنوان المادة في المصدر</label>
                <input 
                  type="text" 
                  name="source_title"
                  value={formData.source_title}
                  onChange={handleChange}
                  className="w-full bg-white/5 border border-white/10 rounded-xl py-3 px-4 focus:outline-none focus:border-indigo-500 transition-all"
                />
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-sm text-slate-400 block">رابط المصدر (URL)</label>
              <input 
                type="url" 
                name="source_url"
                value={formData.source_url}
                onChange={handleChange}
                placeholder="https://..."
                className="w-full bg-white/5 border border-white/10 rounded-xl py-3 px-4 focus:outline-none focus:border-indigo-500 transition-all"
              />
            </div>

            <div className="space-y-2">
              <div className="flex items-center gap-2 mb-1">
                <Music size={14} className="text-amber-400" />
                <label className="text-sm text-slate-400 block">رابط الملف الصوتي</label>
              </div>
              <input 
                type="url" 
                name="audio_url"
                value={formData.audio_url}
                onChange={handleChange}
                placeholder="https://.../audio.mp3"
                className="w-full bg-white/5 border border-white/10 rounded-xl py-3 px-4 focus:outline-none focus:border-indigo-500 transition-all"
              />
            </div>
          </div>
        </div>

        {/* Sidebar Settings Area */}
        <div className="space-y-6">
          <div className="glass-card space-y-6">
            <div className="flex items-center gap-2 text-indigo-400 font-bold mb-2">
              <User size={20} />
              <h3>بيانات المفتي والحالة</h3>
            </div>

            <div className="space-y-2">
              <label className="text-sm text-slate-400 block">اسم العالم/الشيخ</label>
              <select 
                name="scholar_name"
                value={formData.scholar_name}
                onChange={handleChange}
                className="w-full bg-white/5 border border-white/10 rounded-xl py-3 px-4 focus:outline-none focus:border-indigo-500 transition-all appearance-none"
                required
              >
                <option value="" className="bg-slate-900">-- اختر عالماً --</option>
                {scholars.map(s => (
                  <option key={s.id} value={s.name} className="bg-slate-900">{s.name}</option>
                ))}
              </select>
            </div>

            <div className="space-y-2">
              <label className="text-sm text-slate-400 block">حالة النشر</label>
              <div className="grid grid-cols-2 gap-2">
                <button 
                  type="button"
                  onClick={() => setFormData(p => ({ ...p, status: 'published' }))}
                  className={`py-3 rounded-xl border text-sm font-bold transition-all ${
                    formData.status === 'published' 
                    ? 'bg-emerald-500/10 border-emerald-500 text-emerald-400' 
                    : 'bg-white/5 border-white/10 text-slate-400'
                  }`}
                >
                  منشور
                </button>
                <button 
                  type="button"
                  onClick={() => setFormData(p => ({ ...p, status: 'draft' }))}
                  className={`py-3 rounded-xl border text-sm font-bold transition-all ${
                    formData.status === 'draft' 
                    ? 'bg-amber-500/10 border-amber-500 text-amber-400' 
                    : 'bg-white/5 border-white/10 text-slate-400'
                  }`}
                >
                  مسودة
                </button>
              </div>
            </div>
          </div>

          <div className="glass-card space-y-6">
             <div className="flex items-center gap-2 text-indigo-400 font-bold mb-2">
              <BookOpen size={20} />
              <h3>التصنيف</h3>
            </div>
            
            <p className="text-xs text-slate-500 leading-relaxed italic">
              يتم إدارة التصنيفات والمواضيع حالياً عبر البوت. <br />
              سيتم إضافة دعم التعديل الكامل للتصنيفات في التحديث القادم.
            </p>
            
            <div className="flex items-center gap-2 p-3 bg-indigo-500/10 rounded-xl border border-indigo-500/20 text-indigo-400 text-xs">
              <CheckCircle2 size={14} />
              <span>سيتم حفظ بيانات الفتوى وربطها بالتصنيفات المختارة سابقاً.</span>
            </div>
          </div>
        </div>
      </form>
    </div>
  );
};

export default FatwaEditorPage;
