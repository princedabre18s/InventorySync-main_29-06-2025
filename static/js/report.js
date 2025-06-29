// Report Module JavaScript
document.addEventListener('DOMContentLoaded', () => {
    // Set up event listeners for report functionality
    setupReportEventListeners();
});

/**
 * Sets up all event listeners for the report functionality
 */
function setupReportEventListeners() {
    // Report button click handler
    const reportBtn = document.getElementById('open-report-modal');
    if (reportBtn) {
        reportBtn.addEventListener('click', openReportModal);
    }
    
    // Close modal button
    const closeBtn = document.getElementById('closeReportModal');
    if (closeBtn) {
        closeBtn.addEventListener('click', closeReportModal);
    }
    
    // Close when clicking outside modal content
    const modal = document.getElementById('reportModal');
    if (modal) {
        modal.addEventListener('click', function(event) {
            if (event.target === this) {
                closeReportModal();
            }
        });
    }
    
    // Setup tab switching
    const tabs = document.querySelectorAll('.report-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            // Remove active class from all tabs
            tabs.forEach(t => t.classList.remove('active'));
            // Add active class to clicked tab
            tab.classList.add('active');
            
            // Hide all tab content
            const allContent = document.querySelectorAll('.report-tab-content');
            allContent.forEach(content => content.classList.remove('active'));
            
            // Show selected tab content
            const tabContentId = tab.getAttribute('data-tab');
            const tabContent = document.querySelector(`.report-tab-content[data-tab-content="${tabContentId}"]`);
            tabContent.classList.add('active');
            
            // Load reports if view or archive tab
            if (tabContentId === 'view' || tabContentId === 'archive') {
                loadReports();
            }
        });
    });
    
    // Generate report button
    const generateBtn = document.getElementById('generateReport');
    if (generateBtn) {
        generateBtn.addEventListener('click', generateReport);
    }
}

/**
 * Opens the report modal
 */
function openReportModal() {
    const modal = document.getElementById('reportModal');
    if (modal) {
        modal.style.display = 'block';
        // Load reports
        loadReports();
    }
}

/**
 * Closes the report modal
 */
function closeReportModal() {
    const modal = document.getElementById('reportModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

/**
 * Loads the list of reports
 */
function loadReports() {
    fetch('/list-reports')
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                // Display current reports
                displayReports('currentReports', data.current_reports);
                
                // Display archived reports
                displayReports('archivedReports', data.archived_reports);
            } else {
                console.error('Error loading reports:', data.message);
                showError('Failed to load reports. Please try again.');
            }
        })
        .catch(error => {
            console.error('Error fetching reports:', error);
            showError('Failed to connect to the server. Please try again.');
        });
}

/**
 * Displays reports in the specified container
 * @param {string} containerId - The ID of the container element
 * @param {Array} reports - Array of report objects to display
 */
function displayReports(containerId, reports) {
    const container = document.getElementById(containerId);
    
    if (reports && reports.length > 0) {
        let html = '';
        
        // Sort reports by created date (newest first)
        reports.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        
        reports.forEach(report => {
            // Format file size
            let sizeString = 'Unknown size';
            if (report.size) {
                const sizeKB = report.size / 1024;
                if (sizeKB < 1024) {
                    sizeString = `${sizeKB.toFixed(1)} KB`;
                } else {
                    const sizeMB = sizeKB / 1024;
                    sizeString = `${sizeMB.toFixed(1)} MB`;
                }
            }
            
            // Create card for report
            html += `
            <div class="report-card">
                <div class="report-info">
                    <div>
                        <h6><i class="fas fa-file-pdf"></i> ${report.filename}</h6>
                        <div class="report-info-item"><i class="far fa-calendar-alt"></i> ${report.created_at}</div>
                        <div class="report-info-item"><i class="fas fa-weight"></i> ${sizeString}</div>
                    </div>
                </div>
                <div class="report-actions">
                    <a href="${report.download_url}" class="btn-download-report" download>
                        <i class="fas fa-download"></i> Download
                    </a>
                </div>
            </div>
            `;
        });
        
        container.innerHTML = html;
    } else {
        container.innerHTML = `
        <div class="report-empty">
            <i class="fas fa-file-pdf"></i>
            <p>No reports available.</p>
        </div>
        `;
    }
}


/**
 * Generates a new report
 */
function validateEmails(emailList) {
    // Basic email regex
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailList.every(email => emailRegex.test(email));
}

function generateReport() {
    const generateBtn = document.getElementById('generateReport');
    const statusDiv = document.getElementById('generationStatus');
    const emailInput = document.getElementById('reportRecipientEmail');
    // Split emails by comma, trim spaces, and filter out empty strings
    const recipientEmails = emailInput ? emailInput.value.split(',').map(e => e.trim()).filter(e => e) : [];

    // Validate all emails
    if (recipientEmails.length === 0 || !validateEmails(recipientEmails)) {
        showToast("Please enter only valid email addresses, separated by commas.", "error");
        statusDiv.innerHTML = `
        <div class="alert alert-danger mt-3">
            <i class="fas fa-exclamation-circle"></i> Please enter only valid email addresses, separated by commas.
        </div>
        `;
        return;
    }

    // Disable button and show loading state
    generateBtn.disabled = true;
    generateBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Generating Report...';
    statusDiv.innerHTML = `
    <div class="alert alert-info mt-3">
        <i class="fas fa-info-circle"></i> Generating your business report. This may take a minute...
    </div>
    `;

    // Send request to generate report
    fetch('/generate-report', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ recipient_email: recipientEmails })
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            statusDiv.innerHTML = `
            <div class="alert alert-success mt-3">
                <i class="fas fa-check-circle"></i> Report generated successfully!
                <div class="mt-2">
                    <a href="${data.download_url}" class="btn btn-sm btn-primary" download>
                        <i class="fas fa-download"></i> Download Report
                    </a>
                </div>
            </div>
            `;
        } else {
            showToast(data.message || "Failed to generate or email report.", "error");
            statusDiv.innerHTML = `
            <div class="alert alert-danger mt-3">
                <i class="fas fa-exclamation-circle"></i> ${data.message || "Failed to generate or email report."}
            </div>
            `;
        }
    })
    .catch(error => {
        showToast("An error occurred while generating the report.", "error");
        statusDiv.innerHTML = `
        <div class="alert alert-danger mt-3">
            <i class="fas fa-exclamation-circle"></i> An error occurred.
        </div>
        `;
    })
    .finally(() => {
        generateBtn.disabled = false;
        generateBtn.innerHTML = '<i class="fas fa-file-pdf"></i> Generate Business Report';
    });
}

/**
 * Simple email validation
 */
function validateEmail(email) {
    // Basic email regex
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

/**
 * Shows an error message
 * @param {string} message - Error message to display
 */
function showError(message) {
    const statusDiv = document.getElementById('generationStatus');
    statusDiv.innerHTML = `
    <div class="alert alert-danger mt-3">
        <i class="fas fa-exclamation-circle"></i> ${message}
    </div>
    `;
}

/**
 * Formats a date string
 * @param {string} dateStr - Date string to format
 * @returns {string} Formatted date string
 */
function formatDate(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
}

/**
 * Handles toast notifications
 * @param {string} message - Message to display
 * @param {string} type - Type of notification (success, error, info)
 */
function showToast(message, type = 'success') {
    let backgroundColor = '#4caf50'; // success (green)
    
    if (type === 'error') backgroundColor = '#f44336';
    else if (type === 'info') backgroundColor = '#2196f3';
    
    Toastify({
        text: message,
        duration: 3000,
        close: true,
        gravity: "top",
        position: "right",
        backgroundColor: backgroundColor,
        stopOnFocus: true
    }).showToast();
}



document.addEventListener('DOMContentLoaded', () => {
    // Set up event listeners for report functionality
    setupReportEventListeners();

    // --- Schedule Email Form Submission Handler ---
    const scheduleForm = document.getElementById('scheduleForm');
    if (scheduleForm) {
        scheduleForm.onsubmit = function(e) {
            e.preventDefault();

            // Get form values (update IDs if needed)
            const scheduleName = document.getElementById('scheduleName').value;
            const emailRaw = document.getElementById('scheduleEmail').value;
            const frequency = document.getElementById('scheduleFrequency').value;
            const startDate = document.getElementById('scheduleStartDate').value;
            const endDate = document.getElementById('scheduleEndDate').value;
            const sendTime = document.getElementById('scheduleSendTime').value;

            // Split emails by comma, trim spaces, and filter out empty strings
            const recipients = emailRaw.split(',').map(e => e.trim()).filter(e => e);

            // Validate emails using the same regex as in validateEmails
            const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
            if (recipients.length === 0 || !recipients.every(email => emailRegex.test(email))) {
                showToast("Please enter only valid email addresses, separated by commas.", "error");
                return;
            }

            // Combine start date and send time, then convert to UTC ISO string
            const startDateTimeLocal = new Date(`${startDate}T${sendTime}:00`);
            const startTimeUTC = startDateTimeLocal.toISOString();

            // Combine end date and send time, then convert to UTC ISO string
            const endDateTimeLocal = new Date(`${endDate}T${sendTime}:00`);
            const endTimeUTC = endDateTimeLocal.toISOString();

            // Prepare data for backend with all times in UTC
            const data = {
                schedule_name: scheduleName,
                recipients: recipients,
                frequency: frequency,
                start_time: startTimeUTC,
                end_time: endTimeUTC,
                next_run_time: startTimeUTC, // First run time in UTC
                subject: `Scheduled Report: ${scheduleName}`,
                message: `Your scheduled report "${scheduleName}" will be sent ${frequency} from ${startDate} to ${endDate}.`
            };

            // Send request to schedule report
            fetch('/schedule-report', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            })
            .then(res => {
                if (!res.ok) {
                    throw new Error('Failed to save schedule');
                }
                return res.json();
            })
            .then(() => {
                showToast("Schedule saved!", "success");
                // Optionally reload schedules or close modal here
                // Example: loadSchedules(); or closeScheduleModal();
            })
            .catch(error => {
                showToast(`Failed to save schedule: ${error.message}`, "error");
            });
        };
    }

    // Additional code from report.js would continue here...
});