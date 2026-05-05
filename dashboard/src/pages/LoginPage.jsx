import React, { useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { ShieldCheck, Bot } from 'lucide-react';
import { motion } from 'framer-motion';

const LoginPage = () => {
  const { login } = useAuth();

  useEffect(() => {
    // Define the callback function for Telegram Login
    window.onTelegramAuth = (user) => {
      login(user).catch(err => alert(err.message));
    };

    // Load Telegram Script
    const script = document.createElement('script');
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.setAttribute('data-telegram-login', "Fatwa_CMS_Bot"); // Replace with actual bot username
    script.setAttribute('data-size', 'large');
    script.setAttribute('data-radius', '10');
    script.setAttribute('data-onauth', 'onTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    
    const container = document.getElementById('telegram-login-container');
    if (container) {
      container.appendChild(script);
    }

    return () => {
      if (container) container.innerHTML = '';
    };
  }, [login]);

  return (
    <div className="min-h-screen flex items-center justify-center relative overflow-hidden">
      {/* Background Decor */}
      <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-indigo-500/20 blur-[120px] rounded-full"></div>
      <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-blue-500/20 blur-[120px] rounded-full"></div>

      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="glass p-8 rounded-[2rem] w-full max-w-md text-center z-10"
      >
        <div className="flex justify-center mb-6">
          <div className="p-4 bg-indigo-500/10 rounded-2xl">
            <Bot size={48} className="text-indigo-400" />
          </div>
        </div>

        <h1 className="text-3xl font-bold mb-2 tracking-tight">Fatwa CMS</h1>
        <p className="text-slate-400 mb-8">نظام إدارة وأرشفة الفتاوى الشرعية</p>

        <div className="bg-slate-800/50 p-6 rounded-2xl border border-white/5 mb-8">
          <div className="flex items-center gap-3 text-slate-300 mb-4 justify-center">
            <ShieldCheck size={20} className="text-emerald-400" />
            <span className="font-medium">بوابة دخول المسؤولين</span>
          </div>
          
          <div id="telegram-login-container" className="flex justify-center">
            {/* Widget will be injected here */}
          </div>
        </div>

        <p className="text-xs text-slate-500 leading-relaxed">
          يتطلب الدخول صلاحيات المسؤول. <br />
          يرجى تسجيل الدخول عبر حساب تليجرام المرتبط بالبوت.
        </p>
      </motion.div>
    </div>
  );
};

export default LoginPage;
