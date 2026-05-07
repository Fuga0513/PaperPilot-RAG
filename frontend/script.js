const { createApp } = Vue;

// =========================
// Vue App Init
// =========================
createApp({
    // =========================
    // App State
    // =========================
    data() {
        return {
            token: localStorage.getItem('accessToken') || '',
            currentUser: null,
            authMode: 'login',
            authForm: { username: '', password: '', role: 'user', admin_code: '' },
            authLoading: false,
            authError: '',
            authNotice: '',

            activeNav: 'chat',
            papers: [],
            selectedPaper: null,
            selectedPaperDetail: null,
            isParsingPaper: false,
            documents: [],
            sessions: [],
            currentSessionId: 'session_' + Date.now(),
            sessionId: '',

            messages: [],
            userInput: '',
            isLoading: false,
            abortController: null,
            streamingMessageIndex: null,
            isComposing: false,
            citations: [],
            ragTrace: null,
            toolCalls: [],

            selectedFile: null,
            isUploading: false,
            uploadProgress: '',
            uploadSteps: [],
            activeUploadJobId: '',
            uploadPollTimer: null,
            deleteJobs: {},
            deletePollTimers: {},
            deleteRemoveTimers: {},

            loading: { user: false, sessions: false, documents: false, papers: false, paperDetail: false },
            documentsLoading: false,
            errorMessage: ''
        };
    },

    computed: {
        // Returns true when a JWT exists and /auth/me has loaded a user.
        isAuthenticated() {
            return !!this.token && !!this.currentUser;
        },

        // Admins keep access to the existing global document endpoints.
        isAdmin() {
            return this.currentUser?.role === 'admin';
        }
    },

    async mounted() {
        this.sessionId = this.currentSessionId;
        this.configureMarked();
        if (this.token) await this.bootstrapAuthenticatedPage();
    },

    beforeUnmount() {
        this.stopUploadJobPolling();
        this.stopAllDeleteJobPolling();
        Object.values(this.deleteRemoveTimers).forEach(timer => clearTimeout(timer));
    },

    methods: {
        // =========================
        // Auth Helpers
        // =========================
        getToken() {
            // Read the JWT saved after POST /auth/login.
            return localStorage.getItem('accessToken') || '';
        },

        setToken(token) {
            // Persist a JWT locally and mirror it into Vue state.
            this.token = token || '';
            if (this.token) localStorage.setItem('accessToken', this.token);
        },

        clearToken() {
            // Remove local auth state when logging out or receiving 401.
            this.token = '';
            localStorage.removeItem('accessToken');
        },

        authHeaders(extra = {}) {
            // Build headers for authenticated API requests.
            const headers = { ...extra };
            const token = this.getToken();
            if (token) headers.Authorization = `Bearer ${token}`;
            return headers;
        },

        requireAuth() {
            // Guard user actions that require a valid logged-in session.
            if (this.isAuthenticated) return true;
            this.showError('Please log in before continuing.');
            return false;
        },

        // =========================
        // API Helpers
        // =========================
        async apiRequest(url, options = {}) {
            // Shared fetch wrapper that attaches JWT and normalizes errors.
            try {
                const response = await fetch(url, {
                    ...options,
                    headers: this.authHeaders(options.headers || {})
                });
                if (response.status === 401) {
                    this.logout('Login expired. Please log in again.');
                    throw new Error('Login expired. Please log in again.');
                }
                if (!response.ok) {
                    const payload = await response.json().catch(() => ({}));
                    throw new Error(payload.detail || `HTTP ${response.status}`);
                }
                return response;
            } catch (error) {
                this.handleApiError(error);
                throw error;
            }
        },

        async apiGet(url) {
            // GET helper for protected endpoints such as /auth/me and /sessions.
            const response = await this.apiRequest(url);
            return response.json();
        },

        async apiPost(url, data) {
            // JSON POST helper for protected endpoints.
            const response = await this.apiRequest(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data || {})
            });
            return response.json();
        },

        async apiUpload(url, formData) {
            // FormData upload helper; current upload UI uses XHR for progress.
            const response = await this.apiRequest(url, {
                method: 'POST',
                body: formData
            });
            return response.json();
        },

        async apiDelete(url) {
            // DELETE helper for protected endpoints.
            const response = await this.apiRequest(url, { method: 'DELETE' });
            return response.json();
        },

        handleApiError(error) {
            // Central place to surface API failures in the UI.
            this.showError(error.message || 'API request failed.');
        },

        async authFetch(url, options = {}) {
            // Backward-compatible wrapper for older call sites.
            return this.apiRequest(url, options);
        },

        // =========================
        // User / Session Management
        // =========================
        async bootstrapAuthenticatedPage() {
            // Load the minimum data needed after a saved token or login succeeds.
            try {
                await this.loadCurrentUser();
                await this.loadSessions();
                await this.loadPapers();
                if (this.isAdmin) await this.loadDocuments();
            } catch (error) {
                this.showError(error.message);
            }
        },

        async loadCurrentUser() {
            // GET /auth/me and update currentUser.
            this.loading.user = true;
            try {
                this.currentUser = await this.apiGet('/auth/me');
                this.authError = '';
            } finally {
                this.loading.user = false;
            }
        },

        async submitAuthRequest(endpoint, payload) {
            // Login/register use raw fetch because they do not need a JWT yet.
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || 'Authentication failed.');
            return data;
        },

        async login() {
            // POST /auth/login, save token, then load user/session state.
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
            // POST /auth/register and ask the user to log in afterwards.
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
            this.authNotice = 'Registration succeeded. Please log in.';
        },

        async handleAuthSubmit() {
            // Submit the active auth form and show any backend validation errors.
            if (this.authLoading) return;
            if (!this.authForm.username.trim() || !this.authForm.password.trim()) {
                this.authError = 'Username and password are required.';
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
            // Clear local auth and user-owned UI state.
            this.clearToken();
            this.currentUser = null;
            this.messages = [];
            this.sessions = [];
            this.papers = [];
            this.documents = [];
            this.selectedPaper = null;
            this.selectedPaperDetail = null;
            this.citations = [];
            this.ragTrace = null;
            this.toolCalls = [];
            this.activeNav = 'chat';
            this.authError = message;
            this.authNotice = '';
        },

        switchAuthMode() {
            // Toggle between login and registration forms.
            this.authMode = this.authMode === 'login' ? 'register' : 'login';
            this.authError = '';
            this.authNotice = '';
        },

        async loadSessions() {
            // GET /sessions and update the current user's session list.
            this.loading.sessions = true;
            try {
                const data = await this.apiGet('/sessions');
                this.sessions = data.sessions || [];
            } catch (error) {
                this.showError('Failed to load sessions: ' + error.message);
            } finally {
                this.loading.sessions = false;
            }
        },

        async selectSession(sessionId) {
            // GET /sessions/{id}, load messages, and restore inspector state.
            this.currentSessionId = sessionId;
            this.sessionId = sessionId;
            this.activeNav = 'chat';
            try {
                const data = await this.apiGet(`/sessions/${encodeURIComponent(sessionId)}`);
                this.messages = (data.messages || []).map(msg => ({
                    text: msg.content,
                    isUser: msg.type === 'human',
                    ragTrace: msg.rag_trace || null
                }));
                this.syncInspectorFromMessages();
            } catch (error) {
                this.showError('Failed to load session: ' + error.message);
            }
        },

        handleHistory() {
            // Open the history panel and refresh sessions.
            if (!this.requireAuth()) return;
            this.setActivePanel('history');
        },

        handleNewChat() {
            // Start a new client-side session id for /chat/stream.
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
            // Clear the visible chat transcript without deleting persisted history.
            this.messages = [];
            this.citations = [];
            this.ragTrace = null;
            this.toolCalls = [];
        },

        // =========================
        // Document / Paper Library
        // =========================
        setActivePanel(panel) {
            // Switch main panels and load data on demand.
            this.activeNav = panel;
            this.clearError();
            if (panel === 'library') this.loadPapers();
            if (panel === 'history') this.loadSessions();
        },

        async uploadPaperFile(file) {
            // POST /papers/upload, then return the created user-owned Paper detail.
            const formData = new FormData();
            formData.append('file', file);
            const response = await this.apiRequest('/papers/upload', {
                method: 'POST',
                body: formData
            });
            return response.json();
        },

        async loadPapers() {
            // GET /papers and refresh the current user's Paper Library list.
            if (!this.requireAuth()) return;
            this.loading.papers = true;
            try {
                const papers = await this.apiGet('/papers');
                this.papers = Array.isArray(papers) ? papers : [];
                this.syncSelectedPaperAfterRefresh();
            } catch (error) {
                this.showError('Failed to load papers: ' + error.message);
            } finally {
                this.loading.papers = false;
            }
        },

        async loadPaperDetail(paperId) {
            // GET /papers/{paper_id} and show the selected paper detail panel.
            this.loading.paperDetail = true;
            try {
                this.selectedPaperDetail = await this.apiGet(`/papers/${encodeURIComponent(paperId)}`);
            } catch (error) {
                this.showError('Failed to load paper detail: ' + error.message);
            } finally {
                this.loading.paperDetail = false;
            }
        },

        async selectPaper(paper) {
            // Select a paper row and load its protected detail from GET /papers/{id}.
            this.selectedPaper = paper;
            this.selectedPaperDetail = null;
            if (paper?.id) await this.loadPaperDetail(paper.id);
        },

        async parseSelectedPaper() {
            // POST /papers/{id}/parse to re-run section-aware parsing for this paper.
            const paperId = this.selectedPaper?.id;
            if (!paperId || this.isParsingPaper) return;
            this.isParsingPaper = true;
            try {
                const detail = await this.apiPost(`/papers/${encodeURIComponent(paperId)}/parse`, {});
                this.selectedPaperDetail = detail;
                await this.loadPapers();
                this.selectedPaper = this.papers.find(item => item.id === paperId) || detail;
            } catch (error) {
                this.showError('Failed to parse paper: ' + error.message);
                await this.loadPaperDetail(paperId);
            } finally {
                this.isParsingPaper = false;
            }
        },

        formatPaperTitle(paper) {
            // Prefer extracted title; fall back to original or stored filename.
            if (!paper) return 'Untitled paper';
            return paper.title || paper.original_filename || paper.filename || 'Untitled paper';
        },

        syncSelectedPaperAfterRefresh() {
            // Keep selection stable after GET /papers refreshes the list.
            if (!this.selectedPaper) return;
            const next = this.papers.find(item => item.id === this.selectedPaper.id);
            if (next) {
                this.selectedPaper = next;
                return;
            }
            this.selectedPaper = null;
            this.selectedPaperDetail = null;
        },

        async loadDocuments() {
            // GET /documents for admins and mirror results into papers.
            if (!this.isAdmin) {
                this.papers = [];
                return;
            }
            this.loading.documents = true;
            this.documentsLoading = true;
            try {
                const data = await this.apiGet('/documents');
                this.documents = this.mergeDocumentsWithActiveDeletes(data.documents || []);
            } catch (error) {
                this.showError('Failed to load documents: ' + error.message);
            } finally {
                this.loading.documents = false;
                this.documentsLoading = false;
            }
        },

        handleFileSelect(event) {
            // Store selected upload file and reset the progress state.
            const files = event.target.files;
            this.selectedFile = files && files.length > 0 ? files[0] : null;
            this.uploadProgress = '';
            this.uploadSteps = [];
        },

        createUploadSteps() {
            // Return upload pipeline steps shown by the progress UI.
            return [
                { key: 'upload', label: 'Upload file', percent: 0, status: 'pending', message: '' },
                { key: 'cleanup', label: 'Clean old version', percent: 0, status: 'pending', message: '' },
                { key: 'parse', label: 'Parse and chunk', percent: 0, status: 'pending', message: '' },
                { key: 'parent_store', label: 'Store parent chunks', percent: 0, status: 'pending', message: '' },
                { key: 'vector_store', label: 'Embed and index', percent: 0, status: 'pending', message: '' }
            ];
        },

        updateUploadStep(key, percent, status = 'running', message = '') {
            // Update a single upload progress step by key.
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
            // XHR upload to POST /documents/upload/async so progress events work.
            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                const formData = new FormData();
                formData.append('file', file);
                xhr.open('POST', '/documents/upload/async');
                Object.entries(this.authHeaders()).forEach(([key, value]) => xhr.setRequestHeader(key, value));
                xhr.upload.onprogress = event => this.handleUploadProgress(event);
                xhr.onload = () => this.handleUploadComplete(xhr, resolve, reject);
                xhr.onerror = () => reject(new Error('Upload request failed.'));
                xhr.onabort = () => reject(new Error('Upload was cancelled.'));
                xhr.send(formData);
            });
        },

        handleUploadProgress(event) {
            // Convert XHR progress events into the upload step UI.
            if (!event.lengthComputable) return;
            const percent = Math.round((event.loaded / event.total) * 100);
            this.updateUploadStep('upload', percent, 'running', `Uploaded ${percent}%`);
        },

        handleUploadComplete(xhr, resolve, reject) {
            // Parse upload response and handle auth expiry.
            if (xhr.status === 401) {
                this.logout('Login expired. Please log in again.');
                reject(new Error('Login expired. Please log in again.'));
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
            // Upload a user-owned paper through POST /papers/upload.
            if (!this.selectedFile || this.isUploading) return;
            this.isUploading = true;
            this.uploadProgress = 'Uploading...';
            this.uploadSteps = [];
            try {
                const paper = await this.uploadPaperFile(this.selectedFile);
                this.uploadProgress = this.paperUploadResultMessage(paper.status);
                if (paper.status === 'failed') this.showError(this.uploadProgress);
                if (paper.status === 'metadata_failed') this.showError(this.uploadProgress);
                this.selectedFile = null;
                if (this.$refs.fileInput) this.$refs.fileInput.value = '';
                await this.loadPapers();
                await this.selectPaper(paper);
                this.activeNav = 'library';
            } catch (error) {
                this.uploadProgress = 'Upload failed: ' + error.message;
                this.showError(this.uploadProgress);
            } finally {
                this.isUploading = false;
            }
        },

        startUploadJobPolling(jobId) {
            // Poll GET /documents/upload/jobs/{job_id} until completion.
            this.stopUploadJobPolling();
            const poll = async () => {
                try {
                    const job = await this.apiGet(`/documents/upload/jobs/${encodeURIComponent(jobId)}`);
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
            // Copy backend job progress into the upload UI.
            this.uploadProgress = job.message || '';
            if (Array.isArray(job.steps)) this.uploadSteps = job.steps;
        },

        async finishUploadJob() {
            // Reset upload state and refresh the document list.
            this.stopUploadJobPolling();
            this.isUploading = false;
            this.selectedFile = null;
            if (this.$refs.fileInput) this.$refs.fileInput.value = '';
            await this.loadDocuments();
            this.activeNav = 'library';
        },

        stopUploadJobPolling() {
            // Stop the active upload progress polling timer.
            if (this.uploadPollTimer) clearInterval(this.uploadPollTimer);
            this.uploadPollTimer = null;
        },

        mergeDocumentsWithActiveDeletes(nextDocuments) {
            // Preserve in-flight delete rows while refreshing /documents.
            const merged = Array.isArray(nextDocuments) ? [...nextDocuments] : [];
            Object.keys(this.deleteJobs).forEach(filename => {
                const exists = merged.some(doc => doc.filename === filename);
                const current = this.documents.find(doc => doc.filename === filename);
                if (!exists && current) merged.push(current);
            });
            return merged;
        },

        stopAllDeleteJobPolling() {
            // Cleanup any delete timers left from existing document-management UI.
            Object.keys(this.deletePollTimers).forEach(filename => {
                clearInterval(this.deletePollTimers[filename]);
            });
            this.deletePollTimers = {};
        },

        // =========================
        // Chat / SSE
        // =========================
        handleCompositionStart() {
            this.isComposing = true;
        },

        handleCompositionEnd() {
            this.isComposing = false;
        },

        handleKeyDown(event) {
            // Enter sends; Shift+Enter keeps a newline.
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
            // Create a pending assistant message and stream /chat/stream.
            if (!this.requireAuth()) return;
            const text = this.userInput.trim();
            if (!text || this.isLoading || this.isComposing) return;
            this.createPendingAssistantMessage(text);
            await this.startSSEChat({ message: text, session_id: this.currentSessionId });
        },

        createPendingAssistantMessage(text) {
            // Push user message and reserve one assistant message for tokens.
            this.messages.push({ text, isUser: true });
            this.clearInput();
            this.messages.push({ text: '', isUser: false, isThinking: true, ragTrace: null, ragSteps: [] });
            this.streamingMessageIndex = this.messages.length - 1;
            this.isLoading = true;
            this.abortController = new AbortController();
            this.$nextTick(() => this.scrollToBottom());
        },

        async startSSEChat(payload) {
            // POST /chat/stream with fetch so Authorization headers are included.
            try {
                const response = await this.apiRequest('/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                    signal: this.abortController.signal
                });
                await this.readSseStream(response);
                this.finishStreaming();
            } catch (error) {
                this.handleSSEError(error);
            }
        },

        async readSseStream(response) {
            // Read FastAPI StreamingResponse chunks and split SSE frames.
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                buffer = this.consumeSseBuffer(buffer);
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        consumeSseBuffer(buffer) {
            // Consume every complete SSE frame from a text buffer.
            let eventEndIndex;
            while ((eventEndIndex = buffer.indexOf('\n\n')) !== -1) {
                const eventStr = buffer.slice(0, eventEndIndex);
                buffer = buffer.slice(eventEndIndex + 2);
                if (!eventStr.startsWith('data: ')) continue;
                const dataStr = eventStr.slice(6);
                if (dataStr === '[DONE]') continue;
                this.handleSSEMessage(dataStr);
            }
            return buffer;
        },

        handleSSEMessage(event) {
            // Handle one parsed SSE payload from /chat/stream.
            try {
                const data = typeof event === 'string' ? JSON.parse(event) : event;
                if (data.type === 'content') this.appendAssistantToken(data.content);
                if (data.type === 'rag_step') this.appendRagStep(data.step);
                if (data.type === 'trace') this.applyRagTrace(data.rag_trace);
                if (data.type === 'error') this.appendAssistantToken(`\n[Error: ${data.content}]`);
            } catch (error) {
                this.handleSSEError(error);
            }
        },

        appendAssistantToken(token) {
            // Append one streamed assistant token to the pending message.
            const idx = this.streamingMessageIndex;
            if (idx === null || !this.messages[idx]) return;
            this.messages[idx].isThinking = false;
            this.messages[idx].text += token || '';
        },

        appendRagStep(step) {
            // Add one live RAG step to the pending assistant message and tool panel.
            const idx = this.streamingMessageIndex;
            if (idx === null || !this.messages[idx]) return;
            if (!this.messages[idx].ragSteps) this.messages[idx].ragSteps = [];
            this.messages[idx].ragSteps.push(step);
            this.toolCalls = this.messages[idx].ragSteps.map(item => ({
                name: item.label || 'RAG step',
                detail: item.detail || ''
            }));
        },

        finishStreaming() {
            // Reset streaming state after /chat/stream finishes.
            this.isLoading = false;
            this.abortController = null;
            this.streamingMessageIndex = null;
            this.$nextTick(() => this.scrollToBottom());
        },

        handleSSEError(error) {
            // Display SSE errors without breaking the chat panel.
            const idx = this.streamingMessageIndex;
            if (idx !== null && this.messages[idx]) {
                this.messages[idx].isThinking = false;
                if (error.name === 'AbortError') {
                    this.messages[idx].text = this.messages[idx].text || '(Answer stopped)';
                } else {
                    this.messages[idx].text = `Error: ${error.message}`;
                }
            }
            if (error.name !== 'AbortError') this.showError(error.message);
            this.finishStreaming();
        },

        latestThinkingLabel(msg) {
            if (!msg.ragSteps || msg.ragSteps.length === 0) return 'Thinking...';
            return msg.ragSteps[msg.ragSteps.length - 1].label;
        },

        // =========================
        // Citations
        // =========================
        extractCitations(trace) {
            // Citations come only from real retrieved chunks in rag_trace.
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

        // =========================
        // RAG Trace
        // =========================
        applyRagTrace(trace) {
            // Store final trace and derive citations/tool-call summary.
            const idx = this.streamingMessageIndex;
            if (idx !== null && this.messages[idx]) this.messages[idx].ragTrace = trace;
            this.ragTrace = trace;
            this.citations = this.extractCitations(trace);
            this.toolCalls.unshift({
                name: trace?.tool_name || 'search_knowledge_base',
                detail: trace?.retrieval_stage || 'retrieval'
            });
        },

        syncInspectorFromMessages() {
            // Restore right-side panels when a historical session is loaded.
            const lastTraceMsg = [...this.messages].reverse().find(msg => msg.ragTrace);
            this.ragTrace = lastTraceMsg?.ragTrace || null;
            this.citations = this.extractCitations(this.ragTrace);
            this.toolCalls = this.ragTrace ? [{
                name: this.ragTrace.tool_name || 'search_knowledge_base',
                detail: this.ragTrace.retrieval_stage || 'retrieval'
            }] : [];
        },

        // =========================
        // Tool Panels
        // =========================
        // Tool panels are currently placeholder buttons in index.html.
        // The state they will use later is selectedPaper, citations, ragTrace, and toolCalls.

        // =========================
        // Utility Functions
        // =========================
        showError(message) {
            this.errorMessage = message || 'Something went wrong.';
        },

        clearError() {
            this.errorMessage = '';
        },

        configureMarked() {
            // Configure markdown and syntax highlighting for assistant messages.
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

        getFileIcon(fileType) {
            if (fileType === 'PDF') return 'fas fa-file-pdf';
            if (fileType === 'Word') return 'fas fa-file-word';
            if (fileType === 'Excel') return 'fas fa-file-excel';
            return 'fas fa-file';
        },

        getPaperIcon(filename) {
            const lower = (filename || '').toLowerCase();
            if (lower.endsWith('.pdf')) return 'fas fa-file-pdf';
            if (lower.endsWith('.docx')) return 'fas fa-file-word';
            if (lower.endsWith('.txt')) return 'fas fa-file-lines';
            return 'fas fa-file';
        },

        paperUploadResultMessage(status) {
            if (status === 'failed') return 'Paper uploaded, but parsing failed. Check the library status.';
            if (status === 'metadata_failed') return 'Paper parsed, but metadata extraction failed.';
            return 'Paper uploaded, parsed, and metadata extraction finished.';
        },

        formatMetadataValue(value) {
            if (!value) return 'Not available yet.';
            try {
                const parsed = JSON.parse(value);
                if (Array.isArray(parsed)) return parsed.length ? parsed.join('; ') : 'Not available yet.';
            } catch (error) {
                // Plain strings are expected for scalar metadata fields.
            }
            return value || 'Not available yet.';
        },

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
