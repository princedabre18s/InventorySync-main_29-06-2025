document.addEventListener('DOMContentLoaded', () => {
    const sendButton = document.getElementById('sendButton');
    const messageInput = document.getElementById('messageInput');
    const chatMessages = document.getElementById('chatMessages');

    function addMessage(content, type) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${type}`;
    
        if (type === 'ai') {
            messageDiv.innerHTML = marked.parse(content);
        } else if (type === 'loading') {
            messageDiv.innerHTML = `
                <div class="innovative-chat-loader">
                    <div class="particle-swarm">
                        <span class="particle"></span>
                        <span class="particle delay-1"></span>
                        <span class="particle delay-2"></span>
                        <span class="particle delay-3"></span>
                    </div>
                    <span class="loader-text">Analyzing Data Matrix...</span>
                </div>
            `;
        } else {
            messageDiv.innerHTML = content;
        }
    
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    
        // Handle large tables
        const tables = messageDiv.querySelectorAll('table.data-table');
        tables.forEach(table => {
            const tbody = table.querySelector('tbody');
            if (tbody) {
                const rows = tbody.querySelectorAll('tr');
                const totalRows = rows.length;
                let visibleRows = 5;
                if (totalRows > visibleRows) {
                    for (let i = visibleRows; i < totalRows; i++) {
                        rows[i].style.display = 'none';
                    }
                    const seeMoreButton = document.createElement('button');
                    seeMoreButton.textContent = 'See More';
                    seeMoreButton.className = 'see-more-button';
                    seeMoreButton.addEventListener('click', () => {
                        const nextBatch = visibleRows + 5;
                        for (let i = visibleRows; i < nextBatch && i < totalRows; i++) {
                            rows[i].style.display = '';
                        }
                        visibleRows = nextBatch;
                        if (visibleRows >= totalRows) {
                            seeMoreButton.remove();
                        }
                    });
                    table.parentNode.insertBefore(seeMoreButton, table.nextSibling);
                }
            }
        });
    }

    async function sendMessage() {
        const message = messageInput.value.trim();
        if (!message) return;

        addMessage(message, 'user');
        messageInput.value = '';

        // Add loading animation
        addMessage('', 'loading');
        const loadingMessage = chatMessages.lastChild;

        try {
            const response = await fetch('/chatbot', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question: message })
            });
            const data = await response.json();

            // Remove loading animation
            loadingMessage.remove();

            if (data.message) {
                addMessage(data.message, 'ai');
            } else if (data.error) {
                addMessage(`❌ Error: ${data.error}`, 'ai');
            } else {
                addMessage("⚠️ No response received from server.", 'ai');
            }
        } catch (error) {
            // Remove loading animation
            loadingMessage.remove();
            addMessage(`Error: ${error.message}`, 'ai');
        }
    }

    sendButton.addEventListener('click', sendMessage);

    messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });
});