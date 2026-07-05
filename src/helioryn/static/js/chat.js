function getChatMode() {
  if (window.currentChatMode !== undefined) return window.currentChatMode;
  const saved = sessionStorage.getItem('helioryn-mode');
  return saved || 'public';
}

function renderAnswer(text) {
  let html = text
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>');
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\[Document \d+\]/g, (m) => `<strong>${m}</strong>`);
  html = html.replace(/\[Government Source \d+\]/g, (m) => `<strong style="color:var(--accent)">${m}</strong>`);
  html = html.replace(/•/g, '&bull;');
  return html;
}

function stripHtml(html) {
  const div = document.createElement('div');
  div.innerHTML = html;
  return div.textContent || div.innerText || '';
}

function timeAgo(dateStr) {
  const now = new Date();
  const d = new Date(dateStr);
  const diffMs = now - d;
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return diffMin + 'm ago';
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return diffHr + 'h ago';
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return diffDay + 'd ago';
  return d.toLocaleDateString();
}

// ── Sidebar App ─────────────────────────────────────────────

function sidebarApp() {
  return {
    auth: { authenticated: false },
    projects: [],
    sessions: [],
    recentSessions: [],
    projectSessions: {},
    expandedProjectId: null,
    projectsExpanded: true,
    showNewProject: false,
    newProjectName: '',
    sidebarOpen: false,
    dragSessionId: null,
    dragTargetProjectId: null,

    async init() {
      window.sidebarAppInstance = this;
      await this.checkAuth();
      if (this.auth.authenticated) {
        await this.loadSidebar();
      }
    },

    async checkAuth() {
      try {
        const resp = await fetch('/api/auth/status');
        if (resp.ok) {
          this.auth = await resp.json();
          window.heliorynUser = this.auth;
        }
      } catch (e) {
        this.auth = { authenticated: false };
      }
    },

    async loadSidebar() {
      await Promise.all([this.loadProjects(), this.loadRecentSessions()]);
    },

    async loadProjects() {
      try {
        const resp = await fetch('/api/projects');
        if (resp.ok) {
          this.projects = await resp.json();
          for (const proj of this.projects) {
            await this.loadProjectSessions(proj.project_id);
          }
        }
      } catch (e) {}
    },

    async loadProjectSessions(projectId) {
      try {
        const resp = await fetch(`/api/sessions?project_id=${projectId}&limit=50`);
        if (resp.ok) {
          this.projectSessions[projectId] = await resp.json();
        }
      } catch (e) {}
    },

    async loadRecentSessions() {
      try {
        const resp = await fetch('/api/sessions?limit=20');
        if (resp.ok) {
          this.recentSessions = await resp.json();
        }
      } catch (e) {}
    },

    toggleProject(projectId) {
      this.expandedProjectId = this.expandedProjectId === projectId ? null : projectId;
    },

    async createProject() {
      const name = this.newProjectName.trim();
      if (!name) return;
      try {
        const resp = await fetch('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, description: '' }),
        });
        if (resp.ok) {
          this.showNewProject = false;
          this.newProjectName = '';
          await this.loadSidebar();
        }
      } catch (e) {}
    },

    async newChat(projectId) {
      if (!window.chatAppInstance) return;
      await window.chatAppInstance.startNewSession(false, projectId);
      this.sidebarOpen = false;
      await this.loadSidebar();
    },

    async newChatInProject(projectId) {
      await this.newChat(projectId);
    },

    async loadSession(sessionId) {
      if (!window.chatAppInstance) return;
      await window.chatAppInstance.loadSession(sessionId);
      this.sidebarOpen = false;
    },

    async deleteSession(sessionId) {
      try {
        const resp = await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' });
        if (!resp.ok) return;
        if (window.chatAppInstance && window.chatAppInstance.currentSessionId === sessionId) {
          await window.chatAppInstance.startNewSession();
        }
        await this.loadSidebar();
      } catch (e) {}
    },

    onDragStart(event, sessionId) {
      this.dragSessionId = sessionId;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', sessionId);
      event.target.closest('.sidebar-chat-row').classList.add('sidebar-dragging');
    },

    onDragEnd(event) {
      this.dragSessionId = null;
      this.dragTargetProjectId = null;
      event.target.closest('.sidebar-chat-row')?.classList.remove('sidebar-dragging');
      document.querySelectorAll('.sidebar-drop-target').forEach(el => el.classList.remove('sidebar-drop-target'));
    },

    onDragOver(event, projectId) {
      if (!this.dragSessionId) return;
      this.dragTargetProjectId = projectId;
      event.currentTarget.classList.add('sidebar-drop-target');
    },

    onDragLeave(event, projectId) {
      if (event.currentTarget.contains(event.relatedTarget)) return;
      this.dragTargetProjectId = null;
      event.currentTarget.classList.remove('sidebar-drop-target');
    },

    async onDrop(event, projectId) {
      event.currentTarget.classList.remove('sidebar-drop-target');
      const sessionId = this.dragSessionId;
      if (!sessionId) return;
      this.dragSessionId = null;
      this.dragTargetProjectId = null;
      try {
        const resp = await fetch(`/api/sessions/${sessionId}/project`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: projectId }),
        });
        if (resp.ok) {
          await this.loadSidebar();
        }
      } catch (e) {}
    },
  };
}

// ── Main Chat App ───────────────────────────────────────────

function chatApp() {
  return {
    messages: [],
    input: '',
    loading: false,
    govSearch: false,
    govSearching: false,
    auth: { authenticated: false },
    currentSessionId: null,
    sidebarOpen: false,

    async init() {
      window.chatAppInstance = this;

      // Check auth
      try {
        const resp = await fetch('/api/auth/status');
        if (resp.ok) {
          this.auth = await resp.json();
        }
      } catch (e) {}

      if (this.auth.authenticated) {
        // Check URL for session ID
        const params = new URLSearchParams(window.location.search);
        const sessionId = params.get('session');
        if (sessionId) {
          await this.loadSession(sessionId);
          return;
        }
        // Start new session on server
        await this.startNewSession(true);
      } else {
        // Anonymous: use sessionStorage
        const saved = sessionStorage.getItem('helioryn-chat');
        if (saved) {
          try {
            const parsed = JSON.parse(saved);
            this.messages = parsed.messages || [];
          } catch(e) {}
        }
        if (this.messages.length === 0) {
          this.addSystemMessage(
            'Hello! I\'m your Helioryn evidence assistant. I can answer questions about grant compliance, ' +
            'training requirements, policies, and audit evidence. Try asking something like: ' +
            '"Show me evidence for DOJ training requirements" or "What OVC grant conditions apply?"'
          );
        }
        this.$nextTick(() => this.scrollToBottom());
      }
    },

    addSystemMessage(text) {
      this.messages.push({
        role: 'assistant',
        content: renderAnswer(text),
        sources: [],
        showSources: false,
        verification: null,
      });
    },

    scrollToBottom() {
      this.$nextTick(() => {
        const el = this.$refs.messages;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    async startNewSession(silent, projectId) {
      this.messages = [];
      this.currentSessionId = null;
      try {
        const mode = getChatMode();
        const payload = { mode, title: 'New Chat' };
        if (projectId) payload.project_id = projectId;
        const resp = await fetch('/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (resp.ok) {
          const data = await resp.json();
          this.currentSessionId = data.session_id;
          // Update URL without reload
          const url = new URL(window.location);
          url.searchParams.set('session', data.session_id);
          window.history.replaceState({}, '', url);
        }
      } catch (e) {}

      if (!silent) {
        this.addSystemMessage('New chat started. How can I help you?');
      }

      // Add welcome message for first-time
      if (this.messages.length === 0) {
        this.addSystemMessage(
          'Hello! I\'m your Helioryn evidence assistant. I can answer questions about grant compliance, ' +
          'training requirements, policies, and audit evidence. Try asking something like: ' +
          '"Show me evidence for DOJ training requirements" or "What OVC grant conditions apply?"'
        );
      }

      this.$nextTick(() => this.scrollToBottom());
    },

    async loadSession(sessionId) {
      try {
        const resp = await fetch(`/api/sessions/${sessionId}`);
        if (!resp.ok) {
          await this.startNewSession();
          return;
        }
        const data = await resp.json();
        this.currentSessionId = data.session_id;
        this.messages = data.messages || [];

        // Update URL
        const url = new URL(window.location);
        url.searchParams.set('session', sessionId);
        window.history.replaceState({}, '', url);

        this.$nextTick(() => this.scrollToBottom());
      } catch (e) {
        await this.startNewSession();
      }
    },

    async saveSession() {
      if (!this.auth.authenticated || !this.currentSessionId) return;
      try {
        await fetch(`/api/sessions/${this.currentSessionId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ messages: this.messages }),
        });
      } catch (e) {}
    },

    async sendMessage() {
      const q = this.input.trim();
      if (!q || this.loading) return;
      this.input = '';
      this.loading = true;

      // Safety: auto-reset loading after 5 minutes
      this._loadingTimer = setTimeout(() => { this.loading = false; }, 300000);

      if (Array.isArray(this.messages)) {
        this.messages.push({
          role: 'user',
          content: renderAnswer(q),
          sources: [],
          showSources: false,
          verification: null,
        });
      }
      this.scrollToBottom();

      try {
        const mode = getChatMode();
        const body = { question: q, mode: mode };
        if (this.govSearch) {
          body.gov_search = true;
          this.govSearching = true;
        }

        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        this.govSearching = false;

        if (!Array.isArray(this.messages)) {
          this.messages = [];
        }

        let answerHtml = data.answer ? renderAnswer(data.answer) : '<em>No answer received.</em>';
        if (data.verification && data.verification.summary) {
          const v = data.verification.summary;
          const pct = Math.round(v.avg_score * 100);
          if (pct >= 80) {
            answerHtml += '<div class="v-summary verified">✅ Verified against source documents</div>';
          } else if (pct >= 50) {
            answerHtml += '<div class="v-summary plausible">📋 Partially supported — check the source documents below</div>';
          } else {
            answerHtml += '<div class="v-summary unverified">⚠️ Low confidence — review source documents below</div>';
          }
        }

        this.messages.push({
          role: 'assistant',
          content: answerHtml,
          sources: data.sources || [],
          showSources: false,
          verification: data.verification || null,
        });

        // Save to server if authenticated
        if (this.auth.authenticated && this.currentSessionId) {
          const clean = stripHtml(q);
          const title = clean.substring(0, 60).trim();
          try {
            await fetch(`/api/sessions/${this.currentSessionId}`, {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ messages: this.messages, title }),
            });
            if (window.sidebarAppInstance) {
              window.sidebarAppInstance.loadSidebar();
            }
          } catch (e) {}
        } else {
          this.saveState();
        }
      } catch (err) {
        this.govSearching = false;
        if (Array.isArray(this.messages)) {
          this.messages.push({
            role: 'assistant',
            content: '<em>Connection error. Please try again.</em>',
            sources: [],
            showSources: false,
            verification: null,
          });
        }
      } finally {
        clearTimeout(this._loadingTimer);
        this.loading = false;
        this.scrollToBottom();
      }
    },

    saveState() {
      try {
        sessionStorage.setItem('helioryn-chat', JSON.stringify({
          messages: this.messages.slice(-50),
        }));
      } catch(e) {}
    },

    clearChat() {
      this.messages = [];
      if (this.auth.authenticated) {
        this.startNewSession();
      } else {
        this.addSystemMessage('Chat cleared. How can I help you?');
        this.saveState();
      }
    },
  };
}
