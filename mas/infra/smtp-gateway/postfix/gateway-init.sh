#!/bin/bash
set -euo pipefail

: "${AGENT_MAIL_DOMAIN:?AGENT_MAIL_DOMAIN is required}"
: "${HOME_WIREGUARD_IP:?HOME_WIREGUARD_IP is required}"
: "${HOME_STALWART_SMTP_PORT:?HOME_STALWART_SMTP_PORT is required}"
: "${GATEWAY_QUEUE_LIFETIME:?GATEWAY_QUEUE_LIFETIME is required}"
: "${GATEWAY_BOUNCE_QUEUE_LIFETIME:?GATEWAY_BOUNCE_QUEUE_LIFETIME is required}"
: "${GATEWAY_QUEUE_MIN_FREE_KB:?GATEWAY_QUEUE_MIN_FREE_KB is required}"

case "$AGENT_MAIL_DOMAIN" in
  *[!A-Za-z0-9.-]*) echo "gateway Postfix: invalid AGENT_MAIL_DOMAIN" >&2; exit 1 ;;
esac
case "$HOME_WIREGUARD_IP" in
  *[!0-9.]*) echo "gateway Postfix: HOME_WIREGUARD_IP must be IPv4" >&2; exit 1 ;;
esac

escaped_domain="$(printf '%s' "$AGENT_MAIL_DOMAIN" | sed 's/[.[\*^$()+?{|]/\\&/g')"
printf '/^%s$/ smtp:[%s]:%s\n' "$escaped_domain" "$HOME_WIREGUARD_IP" "$HOME_STALWART_SMTP_PORT" >/etc/postfix/transport.regexp

postconf -e "myhostname = ${HOSTNAME:?HOSTNAME is required}"
postconf -e 'inet_protocols = ipv4'
postconf -e 'inet_interfaces = all'
postconf -e 'mydestination = localhost.localdomain, localhost'
postconf -e 'mynetworks = 127.0.0.0/8, [::1]/128'
postconf -e "relay_domains = $AGENT_MAIL_DOMAIN"
postconf -e 'transport_maps = regexp:/etc/postfix/transport.regexp'
postconf -e 'relayhost ='
postconf -e 'default_transport = error:5.4.6 direct Internet MX delivery is disabled'
postconf -e 'relay_transport = smtp'
postconf -e 'smtpd_relay_restrictions = reject_unauth_destination'
postconf -e 'smtpd_recipient_restrictions = reject_non_fqdn_recipient, reject_unknown_recipient_domain, reject_unauth_destination'
postconf -e 'smtpd_client_restrictions = permit'
postconf -e 'smtpd_helo_required = yes'
postconf -e 'disable_vrfy_command = yes'
postconf -e 'enable_original_recipient = yes'
postconf -e "message_size_limit = ${GATEWAY_MESSAGE_SIZE_LIMIT:-26214400}"
postconf -e 'mailbox_size_limit = 0'
postconf -e "maximal_queue_lifetime = $GATEWAY_QUEUE_LIFETIME"
postconf -e "bounce_queue_lifetime = $GATEWAY_BOUNCE_QUEUE_LIFETIME"
postconf -e 'queue_run_delay = 300s'
postconf -e 'minimal_backoff_time = 300s'
postconf -e 'maximal_backoff_time = 1h'
postconf -e "queue_minfree = $GATEWAY_QUEUE_MIN_FREE_KB"
postconf -e 'smtpd_tls_security_level = may'
postconf -e 'smtpd_tls_auth_only = no'
postconf -e 'smtpd_tls_loglevel = 0'
postconf -e 'smtpd_tls_received_header = no'
postconf -e 'smtpd_sasl_auth_enable = no'
postconf -e 'smtpd_sasl_authenticated_header = no'
postconf -e 'debug_peer_level = 0'
postconf -e 'verbose = no'
postconf -e 'maillog_file = /var/log/postfix/maillog'

# Never expose submission or SMTPS. This gateway accepts inbound SMTP only.
postconf -M 'submission/inet='
postconf -M 'smtps/inet='

postfix check
