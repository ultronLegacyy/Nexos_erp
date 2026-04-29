/* ─────────────────────────────────────────────────────────
   Vue.js App — Nexos ERP: Products, Categories & Inventory
   ───────────────────────────────────────────────────────── */
const { createApp, ref, reactive, computed, onMounted, watch, nextTick } = Vue;

const API_BASE = 'http://localhost:8000/api';

const app = createApp({
  setup() {
    // ── Auth State ────────────────────────────────────────
    const isLoggedIn = ref(false);
    const token = ref(localStorage.getItem('nexos_token') || '');
    const refreshToken = ref(localStorage.getItem('nexos_refresh') || '');
    const currentUser = ref(null);
    const loginForm = reactive({ username: '', password: '' });
    const loginError = ref('');
    const loginLoading = ref(false);

    // ── Navigation ────────────────────────────────────────
    const activeTab = ref('categories');

    // ── Data ──────────────────────────────────────────────
    const categories = ref([]);
    const products = ref([]);
    const transactions = ref([]);

    // ── Loading / Error States ────────────────────────────
    const loading = reactive({ categories: false, products: false, transactions: false });
    const forbidden = ref(false);
    const forbiddenMessage = ref('');

    // ── Toasts ────────────────────────────────────────────
    const toasts = ref([]);
    let toastId = 0;

    function showToast(message, type = 'success') {
      const id = ++toastId;
      toasts.value.push({ id, message, type });
      setTimeout(() => { toasts.value = toasts.value.filter(t => t.id !== id); }, 5000);
    }

    // ── Modals ────────────────────────────────────────────
    const showCategoryModal = ref(false);
    const showProductModal = ref(false);
    const showTransactionModal = ref(false);
    const showDeleteConfirm = ref(false);
    const deleteTarget = reactive({ type: '', id: '', name: '' });
    const isEditing = ref(false);

    const categoryForm = reactive({ id: '', name: '', description: '' });
    const productForm = reactive({ id: '', category: '', name: '', description: '', sku: '', price: '', stock: 0 });
    const transactionForm = reactive({ product: '', transaction_type: 'inbound', quantity: 1, reference: '', notes: '' });
    const formErrors = reactive({});
    const formLoading = ref(false);

    // ── Search & Filter ───────────────────────────────────
    const searchQuery = ref('');
    const filterCategory = ref('');

    // ── Axios Instance ────────────────────────────────────
    function authHeaders() {
      return token.value ? { Authorization: `Bearer ${token.value}` } : {};
    }

    async function apiRequest(method, url, data = null) {
      try {
        const config = { method, url: `${API_BASE}${url}`, headers: authHeaders() };
        if (data) config.data = data;
        const response = await axios(config);
        return response;
      } catch (error) {
        if (error.response && error.response.status === 401) {
          const refreshed = await tryRefreshToken();
          if (refreshed) return apiRequest(method, url, data);
          logout();
        }
        if (error.response && error.response.status === 403) {
          forbidden.value = true;
          forbiddenMessage.value = 'No tienes permisos para realizar esta acción.';
        }
        throw error;
      }
    }

    async function tryRefreshToken() {
      if (!refreshToken.value) return false;
      try {
        const res = await axios.post(`${API_BASE}/auth/token/refresh/`, { refresh: refreshToken.value });
        token.value = res.data.access;
        localStorage.setItem('nexos_token', res.data.access);
        if (res.data.refresh) {
          refreshToken.value = res.data.refresh;
          localStorage.setItem('nexos_refresh', res.data.refresh);
        }
        return true;
      } catch { return false; }
    }

    // ── Auth Methods ──────────────────────────────────────
    async function login() {
      loginLoading.value = true; loginError.value = '';
      try {
        const res = await axios.post(`${API_BASE}/auth/token/`, {
          username: loginForm.username, password: loginForm.password,
        });
        token.value = res.data.access;
        refreshToken.value = res.data.refresh;
        localStorage.setItem('nexos_token', res.data.access);
        localStorage.setItem('nexos_refresh', res.data.refresh);
        isLoggedIn.value = true;
        decodeUser();
        await loadAll();
      } catch (error) {
        loginError.value = error.response?.data?.detail || 'Credenciales incorrectas.';
      } finally { loginLoading.value = false; }
    }

    function logout() {
      token.value = ''; refreshToken.value = '';
      localStorage.removeItem('nexos_token'); localStorage.removeItem('nexos_refresh');
      isLoggedIn.value = false; currentUser.value = null;
    }

    function decodeUser() {
      if (!token.value) return;
      try {
        const payload = JSON.parse(atob(token.value.split('.')[1]));
        currentUser.value = { username: payload.username || loginForm.username, role: payload.role, tenant_id: payload.tenant_id };
      } catch { currentUser.value = { username: loginForm.username, role: 'staff' }; }
    }

    // ── CRUD: Categories ──────────────────────────────────
    async function fetchCategories() {
      loading.categories = true; forbidden.value = false;
      try {
        const res = await apiRequest('get', '/catalog/categories/');
        categories.value = res.data.results || res.data;
      } catch (e) {
        if (e.response?.status !== 403) showToast('Error al cargar categorías', 'error');
      } finally { loading.categories = false; }
    }

    function openCategoryModal(cat = null) {
      isEditing.value = !!cat;
      Object.keys(formErrors).forEach(k => delete formErrors[k]);
      if (cat) {
        categoryForm.id = cat.id; categoryForm.name = cat.name; categoryForm.description = cat.description || '';
      } else {
        categoryForm.id = ''; categoryForm.name = ''; categoryForm.description = '';
      }
      showCategoryModal.value = true;
    }

    async function saveCategory() {
      formLoading.value = true;
      Object.keys(formErrors).forEach(k => delete formErrors[k]);
      try {
        const data = { name: categoryForm.name, description: categoryForm.description };
        if (isEditing.value) {
          await apiRequest('patch', `/catalog/categories/${categoryForm.id}/`, data);
          showToast('Categoría actualizada');
        } else {
          await apiRequest('post', '/catalog/categories/', data);
          showToast('Categoría creada');
        }
        showCategoryModal.value = false;
        await fetchCategories();
      } catch (e) {
        if (e.response?.data) {
          Object.entries(e.response.data).forEach(([k, v]) => { formErrors[k] = Array.isArray(v) ? v[0] : v; });
        } else { showToast('Error al guardar categoría', 'error'); }
      } finally { formLoading.value = false; }
    }

    // ── CRUD: Products ────────────────────────────────────
    async function fetchProducts() {
      loading.products = true; forbidden.value = false;
      try {
        let url = '/catalog/products/';
        const params = [];
        if (filterCategory.value) params.push(`category=${filterCategory.value}`);
        if (params.length) url += '?' + params.join('&');
        const res = await apiRequest('get', url);
        products.value = res.data.results || res.data;
      } catch (e) {
        if (e.response?.status !== 403) showToast('Error al cargar productos', 'error');
      } finally { loading.products = false; }
    }

    function openProductModal(prod = null) {
      isEditing.value = !!prod;
      Object.keys(formErrors).forEach(k => delete formErrors[k]);
      if (prod) {
        productForm.id = prod.id; productForm.category = prod.category;
        productForm.name = prod.name; productForm.description = prod.description || '';
        productForm.sku = prod.sku; productForm.price = prod.price; productForm.stock = prod.stock;
      } else {
        productForm.id = ''; productForm.category = '';
        productForm.name = ''; productForm.description = '';
        productForm.sku = ''; productForm.price = ''; productForm.stock = 0;
      }
      showProductModal.value = true;
    }

    async function saveProduct() {
      formLoading.value = true;
      Object.keys(formErrors).forEach(k => delete formErrors[k]);
      try {
        const data = {
          category: productForm.category, name: productForm.name,
          description: productForm.description, sku: productForm.sku,
          price: productForm.price, stock: productForm.stock,
        };
        if (isEditing.value) {
          await apiRequest('patch', `/catalog/products/${productForm.id}/`, data);
          showToast('Producto actualizado');
        } else {
          await apiRequest('post', '/catalog/products/', data);
          showToast('Producto creado');
        }
        showProductModal.value = false;
        await fetchProducts();
      } catch (e) {
        if (e.response?.data) {
          Object.entries(e.response.data).forEach(([k, v]) => { formErrors[k] = Array.isArray(v) ? v[0] : v; });
        } else { showToast('Error al guardar producto', 'error'); }
      } finally { formLoading.value = false; }
    }

    // ── CRUD: Inventory ───────────────────────────────────
    async function fetchTransactions() {
      loading.transactions = true; forbidden.value = false;
      try {
        const res = await apiRequest('get', '/inventory/transactions/');
        transactions.value = res.data.results || res.data;
      } catch (e) {
        if (e.response?.status !== 403) showToast('Error al cargar transacciones', 'error');
      } finally { loading.transactions = false; }
    }

    function openTransactionModal() {
      Object.keys(formErrors).forEach(k => delete formErrors[k]);
      transactionForm.product = ''; transactionForm.transaction_type = 'inbound';
      transactionForm.quantity = 1; transactionForm.reference = ''; transactionForm.notes = '';
      showTransactionModal.value = true;
    }

    async function saveTransaction() {
      formLoading.value = true;
      Object.keys(formErrors).forEach(k => delete formErrors[k]);
      try {
        await apiRequest('post', '/inventory/transactions/', { ...transactionForm });
        showToast('Transacción registrada');
        showTransactionModal.value = false;
        await Promise.all([fetchTransactions(), fetchProducts()]);
      } catch (e) {
        if (e.response?.data) {
          Object.entries(e.response.data).forEach(([k, v]) => { formErrors[k] = Array.isArray(v) ? v[0] : v; });
        } else { showToast('Error al registrar transacción', 'error'); }
      } finally { formLoading.value = false; }
    }

    // ── Delete ────────────────────────────────────────────
    function confirmDelete(type, id, name) {
      deleteTarget.type = type; deleteTarget.id = id; deleteTarget.name = name;
      showDeleteConfirm.value = true;
    }

    async function executeDelete() {
      try {
        const endpoint = deleteTarget.type === 'category' ? '/catalog/categories' : '/catalog/products';
        await apiRequest('delete', `${endpoint}/${deleteTarget.id}/`);
        showToast(`${deleteTarget.name} eliminado`);
        showDeleteConfirm.value = false;
        if (deleteTarget.type === 'category') await fetchCategories();
        else await fetchProducts();
      } catch (e) {
        showToast('Error al eliminar', 'error');
      }
    }

    // ── Computed ───────────────────────────────────────────
    const filteredProducts = computed(() => {
      if (!searchQuery.value) return products.value;
      const q = searchQuery.value.toLowerCase();
      return products.value.filter(p =>
        p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q)
      );
    });

    const filteredCategories = computed(() => {
      if (!searchQuery.value) return categories.value;
      const q = searchQuery.value.toLowerCase();
      return categories.value.filter(c => c.name.toLowerCase().includes(q));
    });

    const stats = computed(() => ({
      totalCategories: categories.value.length,
      totalProducts: products.value.length,
      lowStock: products.value.filter(p => p.stock <= 10).length,
      totalValue: products.value.reduce((s, p) => s + (parseFloat(p.price) * p.stock), 0),
    }));

    const txTypeLabel = (type) => ({ inbound: '📥 Entrada', outbound: '📤 Salida', adjustment: '🔧 Ajuste' }[type] || type);
    const txBadgeClass = (type) => ({ inbound: 'badge-success', outbound: 'badge-danger', adjustment: 'badge-warning' }[type] || 'badge-info');

    // ── Lifecycle ─────────────────────────────────────────
    async function loadAll() {
      await Promise.all([fetchCategories(), fetchProducts(), fetchTransactions()]);
    }

    function switchTab(tab) {
      activeTab.value = tab; forbidden.value = false; searchQuery.value = '';
    }

    onMounted(() => {
      if (token.value) {
        isLoggedIn.value = true; decodeUser(); loadAll();
      }
    });

    watch(filterCategory, () => fetchProducts());

    return {
      isLoggedIn, currentUser, loginForm, loginError, loginLoading, login, logout,
      activeTab, switchTab,
      categories, products, transactions,
      loading, forbidden, forbiddenMessage,
      toasts, showToast,
      showCategoryModal, showProductModal, showTransactionModal, showDeleteConfirm,
      deleteTarget, isEditing,
      categoryForm, productForm, transactionForm, formErrors, formLoading,
      searchQuery, filterCategory,
      openCategoryModal, saveCategory,
      openProductModal, saveProduct,
      openTransactionModal, saveTransaction,
      confirmDelete, executeDelete,
      filteredProducts, filteredCategories, stats,
      txTypeLabel, txBadgeClass,
      fetchCategories, fetchProducts, fetchTransactions,
    };
  },
});

app.mount('#app');
