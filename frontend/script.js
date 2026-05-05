const { createApp } = Vue;

createApp({
    data() {
        return {
            // Auth / Token state
            token: localStorage.getItem('accessToken') || '',
            currentUser: null,
            authMode: 'login',
            authForm: { username: '', password: '', role: 'user', admin_code: '' },
            authLoading: false,
            authError: '',
            authNotice: '',

            // Page state
            activeNav: 'chat',
            papers: [],
            selectedPaper: null,
            documents: [],
            sessions: [],
            currentSessionId: 'session_' + Date.now(),
            sessionId: '',

            // Chat / RAG state
            messages: [],
            userInput: '',
            isLoading: false,
            abortController: null,
            isComposing: false,
            citations: [],
            ragTrace: null,
            toolCalls: [],

            // Upload / document state
            selectedFile: null,
            isUploading: false,
            uploadProgress: '',
            uploadSteps: [],
            activeUploadJobId: '',
            uploadPollTimer: null,
            deleteJobs: {},
            deletePollTimers: {},
            deleteRemoveTimers: {},

            // Loading and errors
            loading: { user: false, sessions: false, documents: false },
            documentsLoading: false,
            errorMessage: ''
        };
    },

    computed: {
        isAuthenticated() {
            return !!this.token && !!this.currentUser;
        },
        isAdmin() {
            return this.currentUser?.role === 'admin';
        }
    },

    async mounted() {
        this.sessionId = this.currentSessionId;
        this.configureMarked();
        if (this.token) {
            await this.bootstrapAuthenticatedPage();
        }
    },

    beforeUnmount() {
        this.stopUploadJobPolling();
        this.stopAllDeleteJobPolling();
        Object.values(this.deleteRemoveTimers).forEach(timer => clearTimeout(timer));
    },

    methods: {
        // Markdown renderer setup for assistant messages.
        configureMarked() {
            marked.setOptions({
                highlight(code, lang) {
                    const language = hljs.getLanguage(lang) ? lang : 'plaintext';
                    return hljs.highlight(code, { language }).value;
                },
                langPrefix: 'hljs language-',
                breaks: true,
                gfm: true
            });
        },

        parseMarkdown(text) {
            return marked.parse(text || '');
        },

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text || '';
            return div.innerHTML;
        },

        // Auth / Token management
        getToken() {
            return localStorage.getItem('accessToken') || '';
        },

        setToken(token) {
            this.token = token || '';
            if (this.token) localStorage.setItem('accessToken', this.token);
        },

        clearToken() {
            this.token = '';
            localStorage.removeItem('accessToken');
        },

        authHeaders(extra = {}) {
            const headers = { ...extra };
            const token = this.getToken();
            if (token) headers.Authorization = `Bearer ${token}`;
            return headers;
        },

        async authFetch(url, options = {}) {
            const opts = { ...options, headers: this.authHeaders(options.headers || {}) };
            const response = await fetch(url, opts);
            if (response.status === 401) {
                this.logout('登录已过期，请重新登录。');
                throw new Error('登录已过期，请重新登录。');
            }
            return response;
        },

        requireAuth() {
            if (this.isAuthenticated) return true;
            this.showError('请先登录后再继续操作。');
            return false;
        },

        showError(message) {
            this.errorMessage = message || '操作失败，请稍后重试。';
        },

        clearError() {
            this.errorMessage = '';
        },

        async bootstrapAuthenticatedPage() {
            try {
                await this.loadCurrentUser();
                await this.loadSessions();
                if (this.isAdmin) await this.loadDocuments();
            } catch (error) {
                this.showError(error.message);
            }
        },

        async loadCurrentUser() {
            this.loading.user = true;
            try {
                const response = await this.authFetch('/auth/me');
                if (!response.ok) throw new Error('认证失败，请重新登录。');
                this.currentUser = await response.json();
                this.authError = '';
            } finally {
                this.loading.user = false;
            }
        },

        async submitAuthRequest(endpoint, payload) {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || '认证请求失败。');
            return data;
        },

        async login() {
            const data = await this.submitAuthRequest('/auth/login', {
                username: this.authForm.username.trim(),
                password: this.authForm.password.trim()
            });
            this.setToken(data.access_token);
            this.currentUser = { username: data.username, role: data.role };
            this.authForm.password = '';
            this.authError = '';
            this.authNotice = '';
            await this.bootstrapAuthenticatedPage();
        },

        async register() {
            const payload = {
                username: this.authForm.username.trim(),
                password: this.authForm.password.trim(),
                role: this.authForm.role
            };
            if (payload.role === 'admin') payload.admin_code = this.authForm.admin_code || null;
            await this.submitAuthRequest('/auth/register', payload);
            this.clearToken();
            this.currentUser = null;
            this.authMode = 'login';
            this.authForm.password = '';
            this.authForm.admin_code = '';
            this.authNotice = '注册成功，请登录。';
        },

        async handleAuthSubmit() {
            if (this.authLoading) return;
            if (!this.authForm.username.trim() || !this.authForm.password.trim()) {
                this.authError = '用户名和密码不能为空。';
                return;
            }
            this.authLoading = true;
            this.authError = '';
            this.authNotice = '';
            try {
                if (this.authMode === 'login') await this.login();
                else await this.register();
            } catch (error) {
                this.authError = error.message;
            } finally {
                this.authLoading = false;
            }
        },

        logout(message = '') {
            this.clearToken();
            this.currentUser = null;
            this.messages = [];
            this.sessions = [];
            this.papers = [];
            this.documents = [];
            this.selectedPaper = null;
            this.citations = [];
            this.ragTrace = null;
            this.toolCalls = [];
            this.activeNav = 'chat';
            this.authError = message;
            this.authNotice = '';
        },

        switchAuthMode() {
            this.authMode = this.authMode === 'login' ? 'register' : 'login';
            this.authError = '';
            this.authNotice = '';
        },

        // Navigation and page loading
        setActivePanel(panel) {
            this.activeNav = panel;
            this.clearError();
            if (panel === 'library' && this.isAdmin) this.loadDocuments();
            if (panel === 'history') this.loadSessions();
        },

        async loadSessions() {
            this.loading.sessions = true;
            try {
                const response = await this.authFetch('/sessions');
                if (!response.ok) throw new Error('Failed to load sessions.');
                const data = await response.json();
                this.sessions = data.sessions || [];
            } catch (error) {
                this.showError('加载会话失败：' + error.message);
            } finally {
                this.loading.sessions = false;
            }
        },

        async loadDocuments() {
            if (!this.isAdmin) {
                this.papers = [];
                return;
            }
            this.loading.documents = true;
            this.documentsLoading = true;
            try {
                const response = await this.authFetch('/documents');
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Failed to load documents.');
                this.documents = this.mergeDocumentsWithActiveDeletes(data.documents || []);
                this.papers = this.documents;
            } catch (error) {
                this.showError('加载文档列表失败：' + error.message);
            } finally {
                this.loading.documents = false;
                this.documentsLoading = false;
            }
        },

        selectPaper(paper) {
            this.selectedPaper = paper;
        },

        async selectSession(sessionId) {
            this.currentSessionId = sessionId;
            this.sessionId = sessionId;
            this.activeNav = 'chat';
            try {
                const response = await this.authFetch(`/sessions/${encodeURIComponent(sessionId)}`);
                if (!response.ok) throw new Error('Failed to load session messages.');
                const data = await response.json();
                this.messages = (data.messages || []).map(msg => ({
                    text: msg.content,
                    isUser: msg.type === 'human',
                    ragTrace: msg.rag_trace || null
                }));
                this.syncInspectorFromMessages();
            } catch (error) {
                this.showError('加载会话失败：' + error.message);
            }
        },

        handleHistory() {
            if (!this.requireAuth()) return;
            this.setActivePanel('history');
        },

        handleNewChat() {
            if (!this.requireAuth()) return;
            this.currentSessionId = 'session_' + Date.now();
            this.sessionId = this.currentSessionId;
            this.messages = [];
            this.citations = [];
            this.ragTrace = null;
            this.toolCalls = [];
            this.activeNav = 'chat';
        },

        handleClearChat() {
            this.messages = [];
            this.citations = [];
            this.ragTrace = null;
            this.toolCalls = [];
        },

        // Chat / SSE streaming
        handleCompositionStart() {
            this.isComposing = true;
        },

        handleCompositionEnd() {
            this.isComposing = false;
        },

        handleKeyDown(event) {
            if (event.key === 'Enter' && !event.shiftKey && !this.isComposing) {
                event.preventDefault();
                this.handleSend();
            }
        },

        clearInput() {
            this.userInput = '';
            this.resetTextareaHeight();
        },

        handleStop() {
            if (this.abortController) this.abortController.abort();
        },

        async handleSend() {
            if (!this.requireAuth()) return;
            const text = this.userInput.trim();
            if (!text || this.isLoading || this.isComposing) return;
            const botMsgIdx = this.createPendingAssistantMessage(text);
            await this.streamChatResponse(text, botMsgIdx);
        },

        createPendingAssistantMessage(text) {
            this.messages.push({ text, isUser: true });
            this.clearInput();
            this.messages.push({ text: '', isUser: false, isThinking: true, ragTrace: null, ragSteps: [] });
            this.isLoading = true;
            this.abortController = new AbortController();
            this.$nextTick(() => this.scrollToBottom());
            return this.messages.length - 1;
        },

        async streamChatResponse(text, botMsgIdx) {
            try {
                const response = await this.authFetch('/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: text, session_id: this.currentSessionId }),
                    signal: this.abortController.signal
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                await this.readSseStream(response, botMsgIdx);
            } catch (error) {
                this.handleChatError(error, botMsgIdx);
            } finally {
                this.isLoading = false;
                this.abortController = null;
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        async readSseStream(response, botMsgIdx) {
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                buffer = this.consumeSseBuffer(buffer, botMsgIdx);
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        consumeSseBuffer(buffer, botMsgIdx) {
            let eventEndIndex;
            while ((eventEndIndex = buffer.indexOf('\n\n')) !== -1) {
                const eventStr = buffer.slice(0, eventEndIndex);
                buffer = buffer.slice(eventEndIndex + 2);
                if (!eventStr.startsWith('data: ')) continue;
                const dataStr = eventStr.slice(6);
                if (dataStr === '[DONE]') continue;
                this.handleSseEvent(dataStr, botMsgIdx);
            }
            return buffer;
        },

        handleSseEvent(dataStr, botMsgIdx) {
            try {
                const data = JSON.parse(dataStr);
                if (data.type === 'content') this.appendAssistantContent(botMsgIdx, data.content);
                if (data.type === 'rag_step') this.appendRagStep(botMsgIdx, data.step);
                if (data.type === 'trace') this.applyRagTrace(botMsgIdx, data.rag_trace);
                if (data.type === 'error') this.appendAssistantContent(botMsgIdx, `\n[Error: ${data.content}]`);
            } catch (error) {
                this.showError('SSE 响应解析失败：' + error.message);
            }
        },

        appendAssistantContent(botMsgIdx, content) {
            this.messages[botMsgIdx].isThinking = false;
            this.messages[botMsgIdx].text += content || '';
        },

        appendRagStep(botMsgIdx, step) {
            if (!this.messages[botMsgIdx].ragSteps) this.messages[botMsgIdx].ragSteps = [];
            this.messages[botMsgIdx].ragSteps.push(step);
            this.toolCalls = this.messages[botMsgIdx].ragSteps.map(item => ({
                name: item.label || 'RAG step',
                detail: item.detail || ''
            }));
        },

        applyRagTrace(botMsgIdx, trace) {
            this.messages[botMsgIdx].ragTrace = trace;
            this.ragTrace = trace;
            this.citations = this.extractCitations(trace);
            this.toolCalls.unshift({
                name: trace?.tool_name || 'search_knowledge_base',
                detail: trace?.retrieval_stage || 'retrieval'
            });
        },

        handleChatError(error, botMsgIdx) {
            this.messages[botMsgIdx].isThinking = false;
            if (error.name === 'AbortError') {
                this.messages[botMsgIdx].text = this.messages[botMsgIdx].text || '(回答已终止)';
                return;
            }
            this.messages[botMsgIdx].text = `出错了：${error.message}`;
            this.showError(error.message);
        },

        latestThinkingLabel(msg) {
            if (!msg.ragSteps || msg.ragSteps.length === 0) return 'Thinking...';
            return msg.ragSteps[msg.ragSteps.length - 1].label;
        },

        extractCitations(trace) {
            if (!trace) return [];
            const chunks = trace.expanded_retrieved_chunks || trace.initial_retrieved_chunks || trace.retrieved_chunks || [];
            return chunks.map(chunk => ({
                filename: chunk.filename,
                page_number: chunk.page_number,
                text: (chunk.text || '').slice(0, 260),
                rrf_rank: chunk.rrf_rank,
                rerank_score: chunk.rerank_score
            }));
        },

        syncInspectorFromMessages() {
            const lastTraceMsg = [...this.messages].reverse().find(msg => msg.ragTrace);
            this.ragTrace = lastTraceMsg?.ragTrace || null;
            this.citations = this.extractCitations(this.ragTrace);
            this.toolCalls = this.ragTrace ? [{
                name: this.ragTrace.tool_name || 'search_knowledge_base',
                detail: this.ragTrace.retrieval_stage || 'retrieval'
            }] : [];
        },

        autoResize(event) {
            const textarea = event.target;
            textarea.style.height = 'auto';
            textarea.style.height = textarea.scrollHeight + 'px';
        },

        resetTextareaHeight() {
            if (this.$refs.textarea) this.$refs.textarea.style.height = 'auto';
        },

        scrollToBottom() {
            if (this.$refs.chatContainer) {
                this.$refs.chatContainer.scrollTop = this.$refs.chatContainer.scrollHeight;
            }
        },

        // Upload / document management
        handleFileSelect(event) {
            const files = event.target.files;
            this.selectedFile = files && files.length > 0 ? files[0] : null;
            this.uploadProgress = '';
            this.uploadSteps = this.createUploadSteps();
        },

        createUploadSteps() {
            return [
                { key: 'upload', label: 'Upload file', percent: 0, status: 'pending', message: '' },
                { key: 'cleanup', label: 'Clean old version', percent: 0, status: 'pending', message: '' },
                { key: 'parse', label: 'Parse and chunk', percent: 0, status: 'pending', message: '' },
                { key: 'parent_store', label: 'Store parent chunks', percent: 0, status: 'pending', message: '' },
                { key: 'vector_store', label: 'Embed and index', percent: 0, status: 'pending', message: '' }
            ];
        },

        updateUploadStep(key, percent, status = 'running', message = '') {
            const idx = this.uploadSteps.findIndex(step => step.key === key);
            if (idx === -1) return;
            this.uploadSteps[idx] = {
                ...this.uploadSteps[idx],
                percent: Math.max(0, Math.min(100, Math.round(percent || 0))),
                status,
                message
            };
        },

        uploadFileWithProgress(file) {
            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                const formData = new FormData();
                formData.append('file', file);
                xhr.open('POST', '/documents/upload/async');
                Object.entries(this.authHeaders()).forEach(([key, value]) => xhr.setRequestHeader(key, value));
                xhr.upload.onprogress = event => this.handleUploadProgress(event);
                xhr.onload = () => this.handleUploadComplete(xhr, resolve, reject);
                xhr.onerror = () => reject(new Error('上传请求失败。'));
                xhr.onabort = () => reject(new Error('上传已取消。'));
                xhr.send(formData);
            });
        },

        handleUploadProgress(event) {
            if (!event.lengthComputable) return;
            const percent = Math.round((event.loaded / event.total) * 100);
            this.updateUploadStep('upload', percent, 'running', `Uploaded ${percent}%`);
        },

        handleUploadComplete(xhr, resolve, reject) {
            if (xhr.status === 401) {
                this.logout('登录已过期，请重新登录。');
                reject(new Error('登录已过期，请重新登录。'));
                return;
            }
            const data = JSON.parse(xhr.responseText || '{}');
            if (xhr.status < 200 || xhr.status >= 300) {
                reject(new Error(data.detail || `HTTP ${xhr.status}`));
                return;
            }
            this.updateUploadStep('upload', 100, 'completed', 'Upload complete');
            resolve(data);
        },

        async uploadDocument() {
            if (!this.selectedFile || this.isUploading) return;
            this.isUploading = true;
            this.uploadProgress = 'Uploading...';
            this.uploadSteps = this.createUploadSteps();
            try {
                const data = await this.uploadFileWithProgress(this.selectedFile);
                this.uploadProgress = data.message;
                this.activeUploadJobId = data.job_id;
                this.startUploadJobPolling(data.job_id);
            } catch (error) {
                this.updateUploadStep('upload', 100, 'failed', error.message);
                this.uploadProgress = 'Upload failed: ' + error.message;
                this.showError(this.uploadProgress);
                this.isUploading = false;
            }
        },

        startUploadJobPolling(jobId) {
            this.stopUploadJobPolling();
            const poll = async () => {
                try {
                    const response = await this.authFetch(`/documents/upload/jobs/${encodeURIComponent(jobId)}`);
                    if (!response.ok) throw new Error('Failed to load upload job.');
                    const job = await response.json();
                    this.syncUploadJob(job);
                    if (job.status === 'completed') await this.finishUploadJob();
                    if (job.status === 'failed') this.isUploading = false;
                } catch (error) {
                    this.uploadProgress = 'Progress check failed: ' + error.message;
                    this.showError(this.uploadProgress);
                    this.isUploading = false;
                    this.stopUploadJobPolling();
                }
            };
            poll();
            this.uploadPollTimer = setInterval(poll, 1000);
        },

        syncUploadJob(job) {
            this.uploadProgress = job.message || '';
            if (Array.isArray(job.steps)) this.uploadSteps = job.steps;
        },

        async finishUploadJob() {
            this.stopUploadJobPolling();
            this.isUploading = false;
            this.selectedFile = null;
            if (this.$refs.fileInput) this.$refs.fileInput.value = '';
            await this.loadDocuments();
            this.activeNav = 'library';
        },

        stopUploadJobPolling() {
            if (this.uploadPollTimer) clearInterval(this.uploadPollTimer);
            this.uploadPollTimer = null;
        },

        mergeDocumentsWithActiveDeletes(nextDocuments) {
            const merged = Array.isArray(nextDocuments) ? [...nextDocuments] : [];
            Object.keys(this.deleteJobs).forEach(filename => {
                const exists = merged.some(doc => doc.filename === filename);
                const current = this.documents.find(doc => doc.filename === filename);
                if (!exists && current) merged.push(current);
            });
            return merged;
        },

        stopAllDeleteJobPolling() {
            Object.keys(this.deletePollTimers).forEach(filename => {
                clearInterval(this.deletePollTimers[filename]);
            });
            this.deletePollTimers = {};
        },

        getFileIcon(fileType) {
            if (fileType === 'PDF') return 'fas fa-file-pdf';
            if (fileType === 'Word') return 'fas fa-file-word';
            if (fileType === 'Excel') return 'fas fa-file-excel';
            return 'fas fa-file';
        },

        // Formatting helpers
        formatDate(value) {
            if (!value) return '-';
            return new Date(value).toLocaleString();
        },

        yesNo(value) {
            if (value === true) return 'Yes';
            if (value === false) return 'No';
            return '-';
        }
    },

    watch: {
        messages: {
            handler() {
                this.$nextTick(() => this.scrollToBottom());
            },
            deep: true
        }
    }
}).mount('#app');
