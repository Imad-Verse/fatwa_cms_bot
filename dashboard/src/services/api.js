import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add a request interceptor to include the Bearer token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('fatwa_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Add a response interceptor to handle errors globally
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('fatwa_token');
      // Optional: Redirect to login or trigger an auth state update
    }
    return Promise.reject(error);
  }
);

export const fatwaService = {
  getStats: () => api.get('/stats'),
  getFatwas: (params) => api.get('/fatwas', { params }),
  getFatwa: (id) => api.get(`/fatwas/${id}`),
  createFatwa: (data) => api.post('/admin/fatwas', data),
  updateFatwa: (id, data) => api.put(`/admin/fatwas/${id}`, data),
  deleteFatwa: (id) => api.delete(`/admin/fatwas/${id}`),
  suggestTags: (text) => api.post('/admin/fatwas/suggest-tags', { text }),
  getScholars: () => api.get('/scholars'),
  getCategories: () => api.get('/categories'),
  getSources: () => api.get('/sources'),
  loginTelegram: (data) => api.post('/auth/telegram', { data }),
};

export default api;
