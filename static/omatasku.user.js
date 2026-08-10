// ==UserScript==
// @name         OmaTasku Partner
// @namespace    OmaTasku
// @version      1.1
// @description  Sünkroniseerib ühe klikiga kuula.postimees.ee premium-küpsise ja tekitab podcastide lehtedele premium RSS-voo nupud!
// @author       OmaTasku
// @match        https://kuula.postimees.ee/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @connect      localhost
// @connect      *
// ==/UserScript==

(function() {
    'use strict';

    // Retrieve stored settings
    let userId = GM_getValue('omatasku_user_id', '');
    let serverUrl = GM_getValue('omatasku_server_url', 'http://localhost:8080');

    // Register Violentmonkey menu options for configuration
    GM_registerMenuCommand('Määra OmaTasku Serveri URL', () => {
        const url = prompt('Sisesta oma OmaTasku serveri aadress (nt. http://localhost:8080):', serverUrl);
        if (url) {
            serverUrl = url.replace(/\/$/, '');
            GM_setValue('omatasku_server_url', serverUrl);
            showToast('⚡ OmaTasku: Serveri URL salvestatud!');
        }
    });

    GM_registerMenuCommand('Määra OmaTasku kasutaja ID (UUID)', () => {
        const uid = prompt('Sisesta oma kordumatu OmaTasku kasutaja ID (UUID):', userId);
        if (uid) {
            userId = uid.trim();
            GM_setValue('omatasku_user_id', userId);
            showToast('⚡ OmaTasku: Kasutaja ID salvestatud!');
        }
    });

    // Extract the active user token __tac cookie
    function getTacCookie() {
        const match = document.cookie.match(/__tac=([^;]+)/);
        return match ? match[1] : null;
    }

    // Displays a gorgeous, modern, non-blocking toast notification
    function showToast(message, isError = false) {
        // Remove existing toast if any
        const oldToast = document.getElementById('omatasku-toast');
        if (oldToast) oldToast.remove();
        
        const toast = document.createElement('div');
        toast.id = 'omatasku-toast';
        toast.innerHTML = message;
        
        const bgColor = isError ? '#3f1f1f' : '#1e293b';
        const textColor = isError ? '#f87171' : '#10b981';
        const borderColor = isError ? 'rgba(239, 68, 68, 0.4)' : 'rgba(16, 185, 129, 0.4)';

        toast.setAttribute('style', `
            position: fixed;
            top: 25px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 100000;
            background-color: ${bgColor};
            color: ${textColor};
            border: 1px solid ${borderColor};
            border-radius: 8px;
            padding: 12px 24px;
            font-size: 14px;
            font-weight: bold;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            animation: omataskuFadeInOut 3.5s ease-in-out forwards;
            white-space: nowrap;
        `);
        
        // Inject animation style block if missing
        if (!document.getElementById('omatasku-animation-style')) {
            const style = document.createElement('style');
            style.id = 'omatasku-animation-style';
            style.innerHTML = `
                @keyframes omataskuFadeInOut {
                    0% { opacity: 0; transform: translate(-50%, -10px); }
                    10% { opacity: 1; transform: translate(-50%, 0); }
                    90% { opacity: 1; transform: translate(-50%, 0); }
                    100% { opacity: 0; transform: translate(-50%, -10px); }
                }
            `;
            document.head.appendChild(style);
        }
        
        document.body.appendChild(toast);
        setTimeout(() => { toast.remove(); }, 3500);
    }

    // Main sync handler
    function syncCookieToOmaTasku() {
        const tac = getTacCookie();
        if (!tac) {
            alert('Viga: Sind ei leitud sisselogituna või __tac küpsist ei leitud. Palun logi esmalt raadioportaali sisse!');
            return;
        }

        if (!userId) {
            const uid = prompt('Palun sisesta oma kordumatu OmaTasku kasutaja ID (UUID) esmakordseks sünkroonimiseks:');
            if (!uid) {
                alert('Viga: Sünkroonimiseks on vajalik OmaTasku kasutaja ID.');
                return;
            }
            userId = uid.trim();
            GM_setValue('omatasku_user_id', userId);
        }

        console.log(`OmaTasku: Sünkroonin küpsise serverisse ${serverUrl} kasutajale ${userId}...`);

        // Perform cross-origin PUT request directly to the OmaTasku database endpoint
        GM_xmlhttpRequest({
            method: 'PUT',
            url: `${serverUrl}/api/users/${userId}`,
            headers: {
                'Content-Type': 'application/json',
                'Origin': window.location.origin
            },
            data: JSON.stringify({
                tac_cookie: tac
            }),
            onload: function(response) {
                if (response.status === 200) {
                    showToast('⚡ OmaTasku: Sinu premium seanss sünkrooniti edukalt!');
                } else {
                    try {
                        const err = JSON.parse(response.responseText);
                        alert(`❌ Viga seansi uuendamisel: ${err.detail || response.statusText}`);
                    } catch (e) {
                        alert(`❌ Viga seansi uuendamisel (kood ${response.status}): ${response.statusText}`);
                    }
                }
            },
            onerror: function(err) {
                alert(`❌ OmaTasku: Ühenduse viga peegeldusserveriga (${serverUrl}).\n\n1. Veendu, et OmaTasku server tõesti töötab.\n2. Veendu, et serveri URL on õige.`);
            }
        });
    }

    // Render floating button for instant sync
    function renderFloatingButton() {
        // Prevent duplicate buttons if DOM re-evaluates
        if (document.getElementById('omatasku-sync-btn')) return;

        const btn = document.createElement('button');
        btn.id = 'omatasku-sync-btn';
        btn.innerHTML = '⚡ OmaTasku Sünk';
        btn.setAttribute('style', `
            position: fixed;
            bottom: 25px;
            right: 25px;
            z-index: 99999;
            background: linear-gradient(135deg, #3b82f6, #0066cc);
            color: white;
            border: none;
            border-radius: 50px;
            padding: 12px 24px;
            font-size: 14px;
            font-weight: bold;
            cursor: pointer;
            box-shadow: 0 4px 15px rgba(0,0,0,0.4);
            transition: all 0.2s ease-in-out;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        `);

        btn.addEventListener('mouseover', () => {
            btn.style.transform = 'translateY(-2px) scale(1.03)';
            btn.style.boxShadow = '0 6px 20px rgba(0,0,0,0.5)';
            btn.style.background = 'linear-gradient(135deg, #2563eb, #0052a3)';
        });
        
        btn.addEventListener('mouseout', () => {
            btn.style.transform = 'translateY(0) scale(1)';
            btn.style.boxShadow = '0 4px 15px rgba(0,0,0,0.4)';
            btn.style.background = 'linear-gradient(135deg, #3b82f6, #0066cc)';
        });
        
        btn.addEventListener('click', syncCookieToOmaTasku);

        document.body.appendChild(btn);
    }

    // Helper to extract the show slug from various platform path patterns
    function extractShowSlug(path) {
        const segments = path.split('/').filter(Boolean);
        if (segments.length >= 2) {
            if (segments[0] === 'kuku') {
                const blacklisted = ['saatekava', 'uudised', 'otsing', 'podcastid'];
                if (!blacklisted.includes(segments[1])) {
                    return segments[1];
                }
            } else if (segments[0] === 'postimees' && segments[1] === 'podcastid' && segments.length >= 3) {
                return segments[2];
            }
        }
        return null;
    }

    // Inject inline copyable RSS button right next to the show/episode heading
    function injectRssButton(showSlug) {
        const h1 = document.querySelector('h1');
        // Verify H1 is present and is not empty or loading
        if (h1 && h1.textContent.trim() && !document.getElementById('omatasku-inline-rss-btn')) {
            const btn = document.createElement('button');
            btn.id = 'omatasku-inline-rss-btn';
            btn.innerHTML = '📻 OmaTasku RSS';
            btn.setAttribute('style', `
                display: inline-flex;
                align-items: center;
                gap: 6px;
                background: linear-gradient(135deg, #10b981, #059669);
                color: white;
                border: none;
                border-radius: 20px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: bold;
                cursor: pointer;
                margin-left: 15px;
                vertical-align: middle;
                box-shadow: 0 2px 6px rgba(0,0,0,0.2);
                transition: all 0.15s ease-in-out;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            `);
            
            btn.addEventListener('mouseover', () => {
                btn.style.transform = 'translateY(-1px)';
                btn.style.boxShadow = '0 4px 10px rgba(0,0,0,0.3)';
                btn.style.background = 'linear-gradient(135deg, #059669, #047857)';
            });
            btn.addEventListener('mouseout', () => {
                btn.style.transform = 'translateY(0)';
                btn.style.boxShadow = '0 2px 6px rgba(0,0,0,0.2)';
                btn.style.background = 'linear-gradient(135deg, #10b981, #059669)';
            });
            
            btn.addEventListener('click', () => {
                if (!userId) {
                    const uid = prompt('Palun sisesta oma kordumatu OmaTasku kasutaja ID (UUID) RSS-linkide tekitamiseks:');
                    if (!uid) return;
                    userId = uid.trim();
                    GM_setValue('omatasku_user_id', userId);
                }
                const rssUrl = `${serverUrl}/${userId}/postimees/rss/shows/${showSlug}`;
                
                // Copy link directly to clipboard
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(rssUrl).then(() => {
                        showToast(`⚡ OmaTasku: Saate "${h1.textContent.trim()}" premium RSS kopeeritud!`);
                    }).catch(() => {
                        alert(`Sinu premium RSS voog on:\n${rssUrl}`);
                    });
                } else {
                    alert(`Sinu premium RSS voog on:\n${rssUrl}`);
                }
            });
            
            // Inject in DOM right after the main H1 heading
            h1.parentNode.insertBefore(btn, h1.nextSibling);
            console.log(`OmaTasku: RSS nupp lisatud saate "${h1.textContent.trim()}" kõrvale.`);
        }
    }

    // Dynamic polling loop to manage SPA state changes seamlessly
    function runLifecycleLoop() {
        // 1. Render floating sync button
        renderFloatingButton();
        
        // 2. Extract show slug if on a show/episode details page
        const showSlug = extractShowSlug(window.location.pathname);
        if (showSlug) {
            injectRssButton(showSlug);
        }
    }

    // Run the lifecycle loop every 1.5 seconds (ultra light-weight, SPA-nav safe)
    setInterval(runLifecycleLoop, 1500);

})();
