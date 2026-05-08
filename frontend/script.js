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
            activeWorkspaceTab: 'chat',
            collapsedEvidenceSections: {
                citations: false,
                trace: false,
                tools: false,
                warnings: false
            },
            quickPromptText: '',
            papers: [],
            selectedPaper: null,
            selectedPaperDetail: null,
            selectedPaperIds: [],
            comparisonQuery: 'Compare the selected papers by problem, method, contribution, dataset, metric, and limitation.',
            comparisonResult: '',
            comparisonAspects: ['problem', 'method', 'contribution', 'dataset', 'metric', 'limitation'],
            isComparingPapers: false,
            reviewComments: '',
            reviewPaperId: '',
            reviewPoints: [],
            rebuttalDraft: '',
            isAnalyzingReview: false,
            isDraftingRebuttal: false,
            rebuttalCopyStatus: '',
            writingTaskTypes: [
                'Generate Related Work',
                'Polish Contributions',
                'Rewrite Abstract',
                'Check Introduction Logic',
                'Polish Grant Scientific Question',
                'Summarize Experimental Settings'
            ],
            writingTaskType: 'Generate Related Work',
            writingTopic: '',
            writingUserText: '',
            writingPaperIds: [],
            writingStyle: 'general academic',
            writingLanguage: 'en',
            writingResult: null,
            isRunningWritingTask: false,
            writingCopyStatus: '',
            evaluationStrategies: ['dense_only', 'bm25_only', 'hybrid', 'hybrid_rerank', 'hybrid_rerank_rewrite'],
            selectedEvaluationStrategies: ['dense_only', 'bm25_only', 'hybrid', 'hybrid_rerank', 'hybrid_rerank_rewrite'],
            evaluationFile: null,
            evaluationName: '',
            evaluationTopK: 5,
            evaluationRuns: [],
            evaluationReport: null,
            evaluationMarkdown: '',
            isRunningEvaluation: false,
            isLoadingEvaluations: false,
            projects: [],
            selectedProjectId: '',
            projectForm: { name: '', description: '' },
            memoryItems: [],
            memoryQuery: '',
            memoryForm: {
                scope: 'global',
                memory_type: 'preference',
                content: '',
                project_id: '',
                paper_id: ''
            },
            isLoadingProjects: false,
            isCreatingProject: false,
            isLoadingMemory: false,
            isSavingMemory: false,
            isParsingPaper: false,
            isIndexingPaper: false,
            deletingPaperIds: [],
            documents: [],
            sessions: [],
            deletingSessionIds: [],
            currentSessionId: 'session_' + Date.now(),
            sessionId: '',

            messages: [],
            userInput: '',
            retrievalScope: 'private',
            isLoading: false,
            abortController: null,
            streamingMessageIndex: null,
            isComposing: false,
            citations: [],
            ragTrace: null,
            traceExpanded: true,
            traceResultsExpanded: false,
            toolCalls: [],

            selectedFile: null,
            isUploading: false,
            uploadProgress: '',
            uploadSteps: [],
            activeUploadJobId: '',
            uploadPollTimer: null,
            deleteJobs: {},
            deletingDocuments: [],
            deletePollTimers: {},
            deleteRemoveTimers: {},

            loading: { user: false, sessions: false, documents: false, papers: false, paperDetail: false, comparison: false, reviewer: false, rebuttal: false, memory: false, evaluation: false },
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
        },

        hasIndexedPapers() {
            return this.papers.some(paper => paper.status === 'indexed');
        },

        selectedPaperCount() {
            return this.selectedPaperIds.length;
        },

        currentUserInitials() {
            const username = this.currentUser?.username || 'U';
            return username.slice(0, 2).toUpperCase();
        },

        retrievalScopeLabel() {
            if (this.retrievalScope === 'global') return 'Global documents';
            if (this.retrievalScope === 'private_plus_global') return 'Private + global';
            return 'Private papers';
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
            this.deletingSessionIds = [];
            this.papers = [];
            this.documents = [];
            this.deleteJobs = {};
            this.deletingDocuments = [];
            this.selectedPaper = null;
            this.selectedPaperDetail = null;
            this.selectedPaperIds = [];
            this.deletingPaperIds = [];
            this.comparisonResult = '';
            this.reviewComments = '';
            this.reviewPaperId = '';
            this.reviewPoints = [];
            this.rebuttalDraft = '';
            this.writingTopic = '';
            this.writingUserText = '';
            this.writingPaperIds = [];
            this.writingResult = null;
            this.evaluationRuns = [];
            this.evaluationReport = null;
            this.evaluationMarkdown = '';
            this.evaluationFile = null;
            this.projects = [];
            this.selectedProjectId = '';
            this.memoryItems = [];
            this.memoryForm.content = '';
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

        async deleteSession(session) {
            // DELETE /sessions/{id}; users can delete only their own chat history.
            const sessionId = session?.session_id || session;
            if (!sessionId || this.deletingSessionIds.includes(sessionId)) return;
            if (!confirm(`Delete session "${sessionId}"?`)) return;
            this.deletingSessionIds = [...this.deletingSessionIds, sessionId];
            this.clearError();
            try {
                await this.apiDelete(`/sessions/${encodeURIComponent(sessionId)}`);
                this.sessions = this.sessions.filter(item => item.session_id !== sessionId);
                if (this.currentSessionId === sessionId) this.handleNewChat();
            } catch (error) {
                this.showError('Failed to delete session: ' + error.message);
            } finally {
                this.deletingSessionIds = this.deletingSessionIds.filter(item => item !== sessionId);
            }
        },

        // =========================
        // Document / Paper Library
        // =========================
        setActivePanel(panel) {
            // Switch main panels and load data on demand.
            this.activeNav = panel;
            this.activeWorkspaceTab = panel;
            this.clearError();
            if (panel === 'library') this.loadPapers();
            if (panel === 'reviewer') this.loadPapers();
            if (panel === 'writing') this.loadPapers();
            if (panel === 'evaluation') this.loadEvaluationRuns();
            if (panel === 'documents') this.loadDocuments();
            if (panel === 'memory') this.loadMemoryPanel();
            if (panel === 'history') this.loadSessions();
        },

        setActiveWorkspace(tab) {
            // Public workspace switcher used by nav/tool shortcuts.
            this.setActivePanel(tab);
        },

        fillQuickPrompt(text) {
            // Fill the chat input from a suggestion chip without sending it.
            this.quickPromptText = text || '';
            this.userInput = this.quickPromptText;
            this.$nextTick(() => {
                if (this.$refs.textarea) {
                    this.$refs.textarea.focus();
                    this.$refs.textarea.style.height = 'auto';
                    this.$refs.textarea.style.height = this.$refs.textarea.scrollHeight + 'px';
                }
            });
        },

        toggleEvidenceSection(sectionName) {
            // Collapse or expand one card in the Evidence & Trace panel.
            this.collapsedEvidenceSections[sectionName] = !this.collapsedEvidenceSections[sectionName];
        },

        isEvidenceSectionCollapsed(sectionName) {
            return !!this.collapsedEvidenceSections[sectionName];
        },

        async loadMemoryPanel() {
            // Load projects, papers, and memory items for the Memory / Project area.
            await Promise.all([this.loadProjects(), this.loadPapers()]);
            await this.loadMemoryItems();
        },

        async loadProjects() {
            // GET /projects and update the current user's project list.
            if (!this.requireAuth()) return;
            this.isLoadingProjects = true;
            try {
                const data = await this.apiGet('/projects');
                this.projects = data.projects || [];
            } catch (error) {
                this.showError('Failed to load projects: ' + error.message);
            } finally {
                this.isLoadingProjects = false;
            }
        },

        async createProject() {
            // POST /projects to create a current-user research project.
            if (!this.requireAuth() || this.isCreatingProject) return;
            if (!this.projectForm.name.trim()) {
                this.showError('Project name is required.');
                return;
            }
            this.isCreatingProject = true;
            try {
                const project = await this.apiPost('/projects', this.projectForm);
                this.projects = [project, ...this.projects];
                this.selectedProjectId = String(project.id);
                this.projectForm = { name: '', description: '' };
                await this.loadMemoryItems();
            } catch (error) {
                this.showError('Failed to create project: ' + error.message);
            } finally {
                this.isCreatingProject = false;
            }
        },

        async loadMemoryItems() {
            // GET /memory and show only the current user's memory items.
            if (!this.requireAuth()) return;
            this.isLoadingMemory = true;
            try {
                const params = new URLSearchParams();
                if (this.memoryQuery.trim()) params.set('query', this.memoryQuery.trim());
                if (this.selectedProjectId) params.set('project_id', this.selectedProjectId);
                const data = await this.apiGet(`/memory${params.toString() ? '?' + params.toString() : ''}`);
                this.memoryItems = data.memories || [];
            } catch (error) {
                this.showError('Failed to load memory: ' + error.message);
            } finally {
                this.isLoadingMemory = false;
            }
        },

        async saveMemoryItem() {
            // POST /memory to save explicit user-approved memory content.
            if (!this.requireAuth() || this.isSavingMemory) return;
            if (!this.memoryForm.content.trim()) {
                this.showError('Memory content is required.');
                return;
            }
            this.isSavingMemory = true;
            try {
                const payload = this.buildMemoryPayload();
                const item = await this.apiPost('/memory', payload);
                this.memoryItems = [item, ...this.memoryItems];
                this.memoryForm.content = '';
            } catch (error) {
                this.showError('Failed to save memory: ' + error.message);
            } finally {
                this.isSavingMemory = false;
            }
        },

        buildMemoryPayload() {
            // Build request body for user-scoped MemoryItem creation.
            const projectId = this.memoryForm.project_id || this.selectedProjectId;
            return {
                scope: this.memoryForm.scope,
                memory_type: this.memoryForm.memory_type,
                content: this.memoryForm.content,
                source_session_id: this.currentSessionId,
                project_id: projectId ? Number(projectId) : null,
                paper_id: this.memoryForm.paper_id ? Number(this.memoryForm.paper_id) : null,
                metadata_json: { saved_from: 'frontend' }
            };
        },

        async deleteMemoryItem(memoryId) {
            // DELETE /memory/{id}; users can delete only their own memory.
            if (!this.requireAuth()) return;
            try {
                await this.apiDelete(`/memory/${encodeURIComponent(memoryId)}`);
                this.memoryItems = this.memoryItems.filter(item => item.id !== memoryId);
            } catch (error) {
                this.showError('Failed to delete memory: ' + error.message);
            }
        },

        fillMemoryFromLatestAssistant() {
            // Copy latest assistant answer into the Save Memory form.
            const latest = [...this.messages].reverse().find(msg => !msg.isUser && msg.text);
            if (!latest) {
                this.showError('No assistant answer is available to save.');
                return;
            }
            this.memoryForm.content = latest.text;
        },

        fillMemoryFromUserInput() {
            // Copy current chat input into the Save Memory form.
            if (!this.userInput.trim()) {
                this.showError('Current input is empty.');
                return;
            }
            this.memoryForm.content = this.userInput;
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
                this.selectedPaperIds = this.selectedPaperIds.filter(id => this.papers.some(paper => paper.id === id));
                this.writingPaperIds = this.writingPaperIds.filter(id => this.papers.some(paper => paper.id === id));
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
                if (detail.status === 'index_failed') {
                    this.showError('Paper parsed successfully, but indexing failed because the Milvus collection uses the old schema. Rebuild the collection, then click Index / Reindex.');
                }
            } catch (error) {
                this.showError('Failed to parse paper: ' + error.message);
                await this.loadPaperDetail(paperId);
            } finally {
                this.isParsingPaper = false;
            }
        },

        async indexSelectedPaper() {
            // POST /papers/{id}/index to write this user's parsed leaf chunks into Milvus.
            const paperId = this.selectedPaper?.id;
            if (!paperId || this.isIndexingPaper) return;
            this.isIndexingPaper = true;
            try {
                const detail = await this.apiPost(`/papers/${encodeURIComponent(paperId)}/index`, {});
                this.selectedPaperDetail = detail;
                this.uploadProgress = detail.status === 'indexed' ? 'Indexing completed.' : '';
                await this.loadPapers();
                this.selectedPaper = this.papers.find(item => item.id === paperId) || detail;
            } catch (error) {
                this.showError('Failed to index paper: ' + error.message);
                await this.loadPaperDetail(paperId);
            } finally {
                this.isIndexingPaper = false;
            }
        },

        async deletePaper(paper) {
            // DELETE /papers/{id}; backend removes only the current user's paper and vectors.
            if (!paper || this.deletingPaperIds.includes(paper.id)) return;
            const title = this.formatPaperTitle(paper);
            if (!confirm(`Delete "${title}" from your Paper Library and RAG index?`)) return;
            this.deletingPaperIds = [...this.deletingPaperIds, paper.id];
            this.clearError();
            try {
                await this.apiDelete(`/papers/${encodeURIComponent(paper.id)}`);
                this.selectedPaperIds = this.selectedPaperIds.filter(id => id !== paper.id);
                this.writingPaperIds = this.writingPaperIds.filter(id => id !== paper.id);
                if (this.selectedPaper?.id === paper.id) {
                    this.selectedPaper = null;
                    this.selectedPaperDetail = null;
                }
                await this.loadPapers();
            } catch (error) {
                this.showError('Failed to delete paper: ' + error.message);
            } finally {
                this.deletingPaperIds = this.deletingPaperIds.filter(id => id !== paper.id);
            }
        },

        paperStatusClass(status) {
            if (status === 'indexed') return 'status-indexed';
            if (status === 'index_failed' || status === 'failed') return 'status-failed';
            if (status === 'indexing' || status === 'parsing') return 'status-running';
            return 'status-pending';
        },

        formatPaperStatus(status) {
            // Normalize backend paper status text for compact badges.
            const value = (status || 'uploaded').replaceAll('_', ' ');
            return value.charAt(0).toUpperCase() + value.slice(1);
        },

        formatPaperTitle(paper) {
            // Prefer extracted title; fall back to original or stored filename.
            if (!paper) return 'Untitled paper';
            return paper.title || paper.original_filename || paper.filename || 'Untitled paper';
        },

        getSelectedPaperSummary() {
            // Return a one-line hint for the composer based on the selected papers.
            if (this.selectedPaperIds.length > 1) return `${this.selectedPaperIds.length} papers selected`;
            if (this.selectedPaper) return `Grounding: ${this.formatPaperTitle(this.selectedPaper)}`;
            return 'No paper selected';
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

        togglePaperSelection(paperId) {
            // Toggle one Paper Library row for POST /papers/compare.
            const id = Number(paperId);
            if (!id) return;
            const exists = this.selectedPaperIds.includes(id);
            if (exists) {
                this.selectedPaperIds = this.selectedPaperIds.filter(item => item !== id);
                return;
            }
            if (this.selectedPaperIds.length >= 5) {
                this.showError('Please select no more than five papers for comparison.');
                return;
            }
            this.selectedPaperIds = [...this.selectedPaperIds, id];
        },

        getSelectedPapers() {
            // Return selected current-user papers from the loaded library state.
            const selected = new Set(this.selectedPaperIds);
            return this.papers.filter(paper => selected.has(paper.id));
        },

        async compareSelectedPapers() {
            // POST /papers/compare and update comparison, citations, and RAG Trace panels.
            if (!this.requireAuth() || this.isComparingPapers) return;
            const selected = this.getSelectedPapers();
            if (selected.length < 2) {
                this.showError('Please select at least two papers to compare.');
                return;
            }
            this.isComparingPapers = true;
            this.loading.comparison = true;
            this.clearError();
            try {
                const result = await this.apiPost('/papers/compare', {
                    query: this.comparisonQuery || 'Compare the selected papers',
                    paper_ids: selected.map(paper => paper.id),
                    compare_aspects: this.comparisonAspects
                });
                this.renderComparisonResult(result);
            } catch (error) {
                this.showError('Failed to compare papers: ' + error.message);
            } finally {
                this.isComparingPapers = false;
                this.loading.comparison = false;
            }
        },

        renderComparisonResult(result) {
            // Render backend Markdown and keep citations/trace consistent with chat answers.
            this.comparisonResult = result?.response || '';
            this.applyCitations(result?.citations || []);
            this.applyRagTrace(result?.rag_trace || null);
            this.toolCalls = result?.tool_calls || this.toolCalls;
        },

        async analyzeReviewerComments() {
            // POST /papers/reviewer/analyze and render structured reviewer-point cards.
            if (!this.requireAuth() || this.isAnalyzingReview) return;
            if (!this.reviewComments.trim()) {
                this.showError('Please paste reviewer comments first.');
                return;
            }
            this.isAnalyzingReview = true;
            this.loading.reviewer = true;
            this.clearError();
            try {
                const result = await this.apiPost('/papers/reviewer/analyze', this.reviewPayload());
                this.renderReviewPoints(result?.points || []);
                this.rebuttalDraft = '';
            } catch (error) {
                this.showError('Failed to analyze reviewer comments: ' + error.message);
            } finally {
                this.isAnalyzingReview = false;
                this.loading.reviewer = false;
            }
        },

        async draftRebuttal() {
            // POST /papers/reviewer/rebuttal and sync citations/RAG Trace with the draft.
            if (!this.requireAuth() || this.isDraftingRebuttal) return;
            if (!this.reviewComments.trim()) {
                this.showError('Please paste reviewer comments first.');
                return;
            }
            this.isDraftingRebuttal = true;
            this.loading.rebuttal = true;
            this.clearError();
            try {
                const result = await this.apiPost('/papers/reviewer/rebuttal', this.reviewPayload());
                this.renderReviewPoints(result?.points || []);
                this.rebuttalDraft = result?.response || '';
                this.applyCitations(result?.citations || []);
                this.applyRagTrace(result?.rag_trace || null);
                this.toolCalls = result?.tool_calls || this.toolCalls;
            } catch (error) {
                this.showError('Failed to draft rebuttal: ' + error.message);
            } finally {
                this.isDraftingRebuttal = false;
                this.loading.rebuttal = false;
            }
        },

        renderReviewPoints(points) {
            // Store normalized reviewer points returned by the backend.
            this.reviewPoints = Array.isArray(points) ? points : [];
        },

        async copyRebuttalDraft() {
            // Copy the current rebuttal draft to the clipboard for editing elsewhere.
            if (!this.rebuttalDraft) return;
            try {
                await navigator.clipboard.writeText(this.rebuttalDraft);
                this.rebuttalCopyStatus = 'Copied';
                setTimeout(() => { this.rebuttalCopyStatus = ''; }, 1600);
            } catch (error) {
                this.showError('Copy failed. Please select the draft text manually.');
            }
        },

        reviewPayload() {
            // Build shared request body for reviewer analysis and rebuttal APIs.
            const paperId = this.reviewPaperId ? Number(this.reviewPaperId) : null;
            return {
                comments: this.reviewComments,
                paper_id: paperId || null
            };
        },

        toggleWritingPaperSelection(paperId) {
            // Toggle one paper for POST /papers/writing/run.
            const id = Number(paperId);
            if (!id) return;
            if (this.writingPaperIds.includes(id)) {
                this.writingPaperIds = this.writingPaperIds.filter(item => item !== id);
                return;
            }
            this.writingPaperIds = [...this.writingPaperIds, id];
        },

        async runWritingTask() {
            // POST /papers/writing/run and render structured writing assistance output.
            if (!this.requireAuth() || this.isRunningWritingTask) return;
            if (!this.writingTopic.trim() && !this.writingUserText.trim() && this.writingPaperIds.length === 0) {
                this.showError('Please provide a topic, text, or selected papers for the writing task.');
                return;
            }
            this.isRunningWritingTask = true;
            this.clearError();
            try {
                const result = await this.apiPost('/papers/writing/run', this.buildWritingPayload());
                this.renderWritingResult(result);
            } catch (error) {
                this.showError('Failed to run writing task: ' + error.message);
            } finally {
                this.isRunningWritingTask = false;
            }
        },

        buildWritingPayload() {
            // Build request body for the research writing API.
            return {
                task_type: this.writingTaskType,
                topic: this.writingTopic,
                user_text: this.writingUserText,
                paper_ids: this.writingPaperIds,
                writing_style: this.writingStyle,
                language: this.writingLanguage
            };
        },

        renderWritingResult(result) {
            // Store structured writing output and sync citation/trace inspectors.
            this.writingResult = result || null;
            this.applyCitations(result?.citations || []);
            this.applyRagTrace(result?.rag_trace || null);
            this.toolCalls = result?.tool_calls || this.toolCalls;
        },

        async copyWritingResult() {
            // Copy the combined research writing result as plain text.
            if (!this.writingResult) return;
            const text = [
                'Evidence-based facts:',
                ...(this.writingResult.evidence_based_facts || []).map(item => `- ${item}`),
                '',
                'Suggested writing:',
                this.writingResult.suggested_writing || '',
                '',
                'Warnings:',
                ...(this.writingResult.warnings || []).map(item => `- ${item}`),
                '',
                'Revision notes:',
                ...(this.writingResult.revision_notes || []).map(item => `- ${item}`)
            ].join('\n');
            try {
                await navigator.clipboard.writeText(text);
                this.writingCopyStatus = 'Copied';
                setTimeout(() => { this.writingCopyStatus = ''; }, 1600);
            } catch (error) {
                this.showError('Copy failed. Please select the result text manually.');
            }
        },

        // =========================
        // Evaluation Report
        // =========================
        handleEvaluationFileSelect(event) {
            // Store the JSONL file that will be sent to POST /evaluation/run.
            this.evaluationFile = event.target.files?.[0] || null;
        },

        toggleEvaluationStrategy(strategy) {
            // Toggle one retrieval ablation strategy for POST /evaluation/run.
            if (this.isRunningEvaluation) return;
            if (this.selectedEvaluationStrategies.includes(strategy)) {
                this.selectedEvaluationStrategies = this.selectedEvaluationStrategies.filter(item => item !== strategy);
                return;
            }
            this.selectedEvaluationStrategies = [...this.selectedEvaluationStrategies, strategy];
        },

        async runEvaluation() {
            // POST /evaluation/run with JSONL upload and render the owned report.
            if (!this.requireAuth() || this.isRunningEvaluation) return;
            if (!this.evaluationFile) {
                this.showError('Please choose a JSONL evaluation dataset.');
                return;
            }
            if (this.selectedEvaluationStrategies.length === 0) {
                this.showError('Please select at least one strategy.');
                return;
            }
            const formData = new FormData();
            formData.append('file', this.evaluationFile);
            formData.append('name', this.evaluationName || this.evaluationFile.name);
            formData.append('strategies', this.selectedEvaluationStrategies.join(','));
            formData.append('top_k', String(this.evaluationTopK || 5));
            this.isRunningEvaluation = true;
            this.clearError();
            try {
                const result = await this.apiUpload('/evaluation/run', formData);
                this.renderEvaluationReport(result);
                await this.loadEvaluationRuns();
            } catch (error) {
                this.showError('Failed to run evaluation: ' + error.message);
            } finally {
                this.isRunningEvaluation = false;
            }
        },

        async loadEvaluationRuns() {
            // GET /evaluation/runs and refresh the current user's evaluation history.
            if (!this.requireAuth()) return;
            this.isLoadingEvaluations = true;
            try {
                const data = await this.apiGet('/evaluation/runs');
                this.evaluationRuns = data.runs || [];
            } catch (error) {
                this.showError('Failed to load evaluation runs: ' + error.message);
            } finally {
                this.isLoadingEvaluations = false;
            }
        },

        async loadEvaluationRun(runId) {
            // GET /evaluation/runs/{id}; backend only returns reports owned by this user.
            if (!runId || this.isLoadingEvaluations) return;
            this.isLoadingEvaluations = true;
            try {
                const result = await this.apiGet(`/evaluation/runs/${encodeURIComponent(runId)}`);
                this.renderEvaluationReport(result);
            } catch (error) {
                this.showError('Failed to load evaluation report: ' + error.message);
            } finally {
                this.isLoadingEvaluations = false;
            }
        },

        loadLatestEvaluationReport() {
            // Load the newest run from the already fetched current-user run list.
            const latest = this.evaluationRuns[0];
            if (!latest) {
                this.showError('No evaluation report is available yet.');
                return;
            }
            this.loadEvaluationRun(latest.id);
        },

        renderEvaluationReport(result) {
            // Store backend JSON/Markdown report for cards, table, and details.
            const report = result?.report || {};
            this.evaluationReport = {
                ...report,
                id: result?.id,
                name: result?.name,
                created_at: result?.created_at,
                metrics: report.metrics || result?.metrics_json || {}
            };
            this.evaluationMarkdown = result?.markdown_report || report.markdown_report || '';
        },

        metricPercent(value) {
            // Render a metric ratio as a readable percentage.
            const num = Number(value || 0);
            return (num * 100).toFixed(1) + '%';
        },

        async loadDocuments() {
            // GET /documents for admins to manage the global SuperMew knowledge base.
            if (!this.isAdmin) {
                this.documents = [];
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

        async uploadGlobalDocument() {
            // POST /documents/upload/async; admins upload into the global document RAG store.
            if (!this.isAdmin || !this.selectedFile || this.isUploading) return;
            this.isUploading = true;
            this.uploadProgress = 'Uploading global document...';
            this.uploadSteps = this.createUploadSteps();
            this.updateUploadStep('upload', 1, 'running', 'Starting upload');
            try {
                const job = await this.uploadFileWithProgress(this.selectedFile);
                this.activeUploadJobId = job.job_id;
                this.startUploadJobPolling(job.job_id);
            } catch (error) {
                this.uploadProgress = 'Global document upload failed: ' + error.message;
                this.showError(this.uploadProgress);
                this.isUploading = false;
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
                if (paper.status === 'index_failed') this.showError(this.uploadProgress);
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
            if (this.$refs.globalFileInput) this.$refs.globalFileInput.value = '';
            await this.loadDocuments();
            this.activeNav = 'documents';
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

        async deleteGlobalDocument(filename) {
            // DELETE /documents/delete/async/{filename}; admin-only global document deletion.
            if (!this.isAdmin || !filename || this.deletingDocuments.includes(filename)) return;
            if (!confirm(`Delete global RAG document "${filename}" and its vectors?`)) return;
            this.deletingDocuments = [...this.deletingDocuments, filename];
            this.clearError();
            try {
                const job = await this.apiDelete(`/documents/delete/async/${encodeURIComponent(filename)}`);
                this.deleteJobs[filename] = { ...job, status: 'running' };
                this.startDeleteJobPolling(filename, job.job_id);
            } catch (error) {
                this.deletingDocuments = this.deletingDocuments.filter(item => item !== filename);
                this.showError('Failed to start document deletion: ' + error.message);
            }
        },

        startDeleteJobPolling(filename, jobId) {
            // Poll GET /documents/delete/jobs/{job_id} and update the global document row.
            if (this.deletePollTimers[filename]) clearInterval(this.deletePollTimers[filename]);
            const poll = async () => {
                try {
                    const job = await this.apiGet(`/documents/delete/jobs/${encodeURIComponent(jobId)}`);
                    this.deleteJobs[filename] = job;
                    if (job.status === 'completed') await this.finishDeleteJob(filename);
                    if (job.status === 'failed') this.failDeleteJob(filename, job.error || job.message);
                } catch (error) {
                    this.failDeleteJob(filename, error.message);
                }
            };
            poll();
            this.deletePollTimers[filename] = setInterval(poll, 1000);
        },

        async finishDeleteJob(filename) {
            // Remove completed delete job state after refreshing /documents.
            if (this.deletePollTimers[filename]) clearInterval(this.deletePollTimers[filename]);
            delete this.deletePollTimers[filename];
            this.deletingDocuments = this.deletingDocuments.filter(item => item !== filename);
            await this.loadDocuments();
            this.deleteRemoveTimers[filename] = setTimeout(() => {
                delete this.deleteJobs[filename];
                delete this.deleteRemoveTimers[filename];
            }, 2000);
        },

        failDeleteJob(filename, message) {
            // Surface async delete failures without removing the document row.
            if (this.deletePollTimers[filename]) clearInterval(this.deletePollTimers[filename]);
            delete this.deletePollTimers[filename];
            this.deletingDocuments = this.deletingDocuments.filter(item => item !== filename);
            this.deleteJobs[filename] = { ...(this.deleteJobs[filename] || {}), status: 'failed', message };
            this.showError(`Failed to delete ${filename}: ${message}`);
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
            if (this.retrievalScope !== 'global' && this.papers.length > 0 && !this.hasIndexedPapers) {
                this.showError('No indexed papers are available yet. Please index a paper before chatting with your Paper Library.');
                this.activeNav = 'library';
                return;
            }
            this.createPendingAssistantMessage(text);
            await this.startSSEChat({
                message: text,
                session_id: this.currentSessionId,
                retrieval_scope: this.retrievalScope
            });
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
                if (data.type === 'answer_delta') this.appendAssistantToken(data.content || data.delta);
                if (data.type === 'rag_step') this.appendRagStep(data.step);
                if (data.type === 'citations') this.applyCitations(data.citations || []);
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
            if (Array.isArray(trace.citations)) return trace.citations;
            const chunks = trace.expanded_retrieved_chunks || trace.initial_retrieved_chunks || trace.retrieved_chunks || [];
            return chunks.map((chunk, index) => ({
                citation_id: chunk.citation_id || `C${index + 1}`,
                paper_id: chunk.paper_id,
                paper_title: chunk.paper_title || chunk.filename,
                filename: chunk.filename,
                section_title: chunk.section_title || '',
                page_start: chunk.page_start || chunk.page_number,
                page_end: chunk.page_end || chunk.page_number,
                chunk_id: chunk.chunk_id || '',
                preview_text: (chunk.text || '').slice(0, 260),
                score: chunk.score,
                rerank_score: chunk.rerank_score
            }));
        },

        applyCitations(citations) {
            // Store backend-returned citations; the frontend never fabricates them.
            this.citations = Array.isArray(citations) ? citations : [];
        },

        formatCitationPages(item) {
            if (!item) return '';
            if (item.page_start && item.page_end && item.page_start !== item.page_end) {
                return `Pages ${item.page_start}-${item.page_end}`;
            }
            const page = item.page_start || item.page_end;
            return page ? `Page ${page}` : 'Page N/A';
        },

        renderCitationPreview(citation) {
            // Keep citation cards dense by clipping long evidence snippets.
            const text = citation?.preview_text || citation?.text || 'No preview text returned.';
            return text.length > 260 ? text.slice(0, 260).trim() + '...' : text;
        },

        async openCitationPaper(item) {
            // Jump to Paper Detail for citations that include a current-user paper_id.
            if (!item?.paper_id) return;
            const paper = this.papers.find(row => row.id === item.paper_id) || { id: item.paper_id };
            this.activeNav = 'library';
            await this.selectPaper(paper);
        },

        // =========================
        // RAG Trace
        // =========================
        applyRagTrace(trace) {
            // Store final trace and derive citations/tool-call summary.
            const idx = this.streamingMessageIndex;
            if (idx !== null && this.messages[idx]) this.messages[idx].ragTrace = trace;
            this.ragTrace = trace;
            if (!trace) {
                this.citations = [];
                this.toolCalls = [];
                return;
            }
            this.applyCitations(this.extractCitations(trace));
            const traceToolCalls = Array.isArray(trace?.tool_calls) ? trace.tool_calls : [];
            this.toolCalls = traceToolCalls.length ? traceToolCalls : [{
                name: trace?.tool_name || 'search_knowledge_base',
                detail: trace?.retrieval_stage || 'retrieval'
            }];
        },

        traceResults(key) {
            // Return a safe trace result list for the collapsible RAG Trace panel.
            const value = this.ragTrace?.[key];
            return Array.isArray(value) ? value : [];
        },

        traceChunkLabel(chunk, index) {
            // Compact label for one retrieved chunk in the trace panel.
            const citation = chunk.citation_id ? `[${chunk.citation_id}] ` : '';
            const title = chunk.paper_title || chunk.filename || 'Unknown source';
            const section = chunk.section_title ? ` - ${chunk.section_title}` : '';
            return `${index + 1}. ${citation}${title}${section}`;
        },

        formatScore(value) {
            if (value === null || value === undefined || value === '') return '-';
            const number = Number(value);
            return Number.isFinite(number) ? number.toFixed(4) : String(value);
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
            if (status === 'index_failed') return 'Paper parsed, but indexing failed. Check Milvus schema and retry indexing.';
            if (status === 'indexed') return 'Paper uploaded, parsed, metadata extracted, and indexed.';
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
        },

        hasActiveWarnings() {
            // The current UI treats the shared error banner as the active warning source.
            return !!(this.errorMessage && this.errorMessage.trim());
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
