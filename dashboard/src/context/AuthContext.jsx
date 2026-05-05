import React, { createContext, useContext, useState, useEffect } from 'react';
import { fatwaService } from '../services/api';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const checkAuth = async () => {
      const token = localStorage.getItem('fatwa_token');
      if (token) {
        try {
          const response = await fatwaService.getStats(); // Just to verify token
          // In a real app, we might have a /me endpoint
          const storedUser = JSON.parse(localStorage.getItem('fatwa_user'));
          setUser(storedUser);
        } catch (error) {
          localStorage.removeItem('fatwa_token');
          localStorage.removeItem('fatwa_user');
        }
      }
      setLoading(false);
    };
    checkAuth();
  }, []);

  const login = async (telegramData) => {
    try {
      const response = await fatwaService.loginTelegram(telegramData);
      const { token, user: userData, is_admin } = response.data;
      
      if (!is_admin) {
        throw new Error('Access denied: You are not an administrator.');
      }

      localStorage.setItem('fatwa_token', token);
      localStorage.setItem('fatwa_user', JSON.stringify(userData));
      setUser(userData);
      return userData;
    } catch (error) {
      console.error('Login failed:', error);
      throw error;
    }
  };

  const logout = () => {
    localStorage.removeItem('fatwa_token');
    localStorage.removeItem('fatwa_user');
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
