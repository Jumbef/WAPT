; Template for waptagent build using iscc
#define edition "waptagent"
#define default_repo_url "https://store.wapt.fr/wapt"
#define default_wapt_server ""
#define repo_url ""
#define wapt_server ""
#define AppName "WAPTAgent"
#define AppId "WAPT"
#define output_dir "."
#define Company "Tranquil IT Systems"
#define send_usage_report 1
; if not empty, set value 0 or 1 will be defined in wapt-get.ini
#define set_use_kerberos "0"

; if empty, a task is added
; copy authorized package certificates (CA or signers) in <wapt>\ssl
#define set_install_certs "1"

; if 1, expiry and CRL of package certificates will be checked
#define check_certificates_validity "1"

; if not empty, the 0, 1 or path to a CA bundle will be defined in wapt-get.ini for checking of https certificates
#define set_verify_cert "0"

; default value for detection server and repo URL using dns 
#define default_dnsdomain ""

; if not empty, a task will propose to install this package or list of packages (comma separated)
#define set_start_packages ""

;#define signtool "kSign /d $qWAPT Client$q /du $qhttp://www.tranquil-it-systems.fr$q $f"

#ifndef set_disable_hiberboot
#define set_disable_hiberboot ""
#endif

; for fast compile in developent mode
;#define FastDebug

;#define waptenterprise

#include "waptsetup.iss"
