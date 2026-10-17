/* hook.c — shim LD_PRELOAD façon « Steam overlay » : la souris du jeu est
 * relâchée DE L'INTÉRIEUR du processus, sans changement de focus (donc sans
 * miniaturisation). Chargé par Ludodex au lancement de chaque jeu.
 *
 * Principe (spike validé le 2026-10-17) :
 *  1. libération ACTIVE quand l'overlay s'ouvre — appel des vraies fonctions
 *     SDL (dlsym RTLD_NEXT) SUR LE THREAD DU JEU (via les hooks d'événements,
 *     l'appel SDL hors thread principal n'étant pas sûr) ;
 *  2. PORTE : tant que l'overlay est ouvert, les ré-assertions de capture du
 *     jeu sont avalées (return succès sans grab) ;
 *  3. à la fermeture, le jeu reprend la main tout seul à sa prochaine
 *     assertion (aucune action côté hook).
 *
 * État lu dans ~/.local/share/ludodex-hook/etat (« 1 » ouvert / « 0 » fermé),
 * écrit par l'overlay — sondé toutes les 300 ms (multi-jeux sans blocage).
 *
 * Périmètre v1 : SDL2 complet ; SDL3 en best-effort (API renommée, fenêtre
 * inconnue) ; X11 (XGrabPointer / XFixesHideCursor) en porte seule — la
 * libération active X11 exigerait des appels Xlib sur la connexion du jeu
 * (non thread-safe sans XInitThreads).
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static volatile int ouvert = 0;     /* 1 = overlay ouvert : jamais de capture */
static volatile int a_relacher = 0; /* demande de libération active */

typedef int (*f_set_rel)(int);
typedef int (*f_show)(int);
static f_set_rel vrai_set_rel;
static f_show vrai_show;

static void charger(void) {
    if (!vrai_set_rel) vrai_set_rel = (f_set_rel)dlsym(RTLD_NEXT, "SDL_SetRelativeMouseMode");
    if (!vrai_show) vrai_show = (f_show)dlsym(RTLD_NEXT, "SDL_ShowCursor");
}

/* Libération appelée SUR LE THREAD DU JEU (depuis les hooks d'événements). */
static void relacher(void) {
    charger();
    if (vrai_show) vrai_show(1);   /* SDL_ENABLE : curseur visible */
    if (vrai_set_rel) vrai_set_rel(0);  /* SDL_FALSE : grab relâché */
    fprintf(stderr, "[ludodex-hook] souris relachee (thread jeu)\n");
    fflush(stderr);
}

static int lire_etat(void) {
    char buf[4] = {0};
    const char *home = getenv("HOME");
    if (!home) return 0;
    char chemin[512];
    snprintf(chemin, sizeof chemin, "%s/.local/share/ludodex-hook/etat", home);
    int fd = open(chemin, O_RDONLY);
    if (fd < 0) return 0;
    ssize_t n = read(fd, buf, 1);
    close(fd);
    return (n == 1 && buf[0] == '1');
}

/* Sondeur d'état (thread dédié) : ne touche JAMAIS à SDL ici. */
static void *sondeur(void *_arg) {
    (void)_arg;
    for (;;) {
        int e = lire_etat();
        if (e != ouvert) {
            ouvert = e;
            a_relacher = ouvert;
            fprintf(stderr, "[ludodex-hook] overlay %s\n", ouvert ? "OUVERT" : "FERME");
            fflush(stderr);
        }
        usleep(300000);
    }
    return NULL;
}

__attribute__((constructor))
static void init(void) {
    charger();
    pthread_t th;
    pthread_create(&th, NULL, sondeur, NULL);
    pthread_detach(th);
}

/* ------------------------------------------------- SDL2 : porte + sortie */

int SDL_SetRelativeMouseMode(int enabled) {
    charger();
    if (ouvert) return 0;              /* overlay ouvert : jamais de capture */
    return vrai_set_rel ? vrai_set_rel(enabled) : -1;
}

/* SDL2 : int SDL_ShowCursor(int toggle) — SDL3 : bool SDL_ShowCursor(void) :
 * même nom, ABI quasi compatible (l'arg superflu est ignoré à l'appel). */
int SDL_ShowCursor(long toggle) {
    charger();
    if (ouvert) return 1;              /* ni cacher (SDL2) ni re-cacher (SDL3) */
    return vrai_show ? vrai_show((int)toggle) : -1;
}

/* ------------------------------------------------- SDL3 : best-effort */

int SDL_SetWindowRelativeMouseMode(void *fenetre, int enabled) {
    static int (*vrai)(void *, int);
    if (!vrai) vrai = (int (*)(void *, int))dlsym(RTLD_NEXT, "SDL_SetWindowRelativeMouseMode");
    if (ouvert) return 0;
    return vrai ? vrai(fenetre, enabled) : -1;
}

int SDL_HideCursor(void) {
    static int (*vrai)(void);
    if (!vrai) vrai = (int (*)(void))dlsym(RTLD_NEXT, "SDL_HideCursor");
    if (ouvert) return 1;              /* prétendu masqué, curseur laissé visible */
    return vrai ? vrai() : -1;
}

/* ---------------------------------------------------- X11 : porte seule */

int XGrabPointer(void *dpy, unsigned long grab_window, int owner_events,
                 unsigned int event_mask, unsigned int pointer_mode,
                 unsigned int keyboard_mode, unsigned long confine_to,
                 unsigned long cursor, unsigned long time) {
    static int (*vrai)(void *, unsigned long, int, unsigned int, unsigned int,
                       unsigned int, unsigned long, unsigned long, unsigned long);
    if (!vrai) vrai = (int (*)(void *, unsigned long, int, unsigned int, unsigned int,
                               unsigned int, unsigned long, unsigned long, unsigned long))
                      dlsym(RTLD_NEXT, "XGrabPointer");
    if (ouvert) return 0;              /* 0 = Success, sans grab */
    return vrai ? vrai(dpy, grab_window, owner_events, event_mask, pointer_mode,
                       keyboard_mode, confine_to, cursor, time) : 0;
}

int XFixesHideCursor(void *dpy, unsigned long win) {
    static int (*vrai)(void *, unsigned long);
    if (!vrai) vrai = (int (*)(void *, unsigned long))dlsym(RTLD_NEXT, "XFixesHideCursor");
    if (ouvert) return 0;              /* curseur non masqué */
    return vrai ? vrai(dpy, win) : 0;
}

/* Libération active sur le thread du jeu : interception de ses appels
 * d'événements (fréquents, thread principal — spike validé). */
int SDL_PollEvent(void *e) {
    static int (*vrai)(void *);
    if (!vrai) vrai = (int (*)(void *))dlsym(RTLD_NEXT, "SDL_PollEvent");
    if (a_relacher) { a_relacher = 0; relacher(); }
    return vrai ? vrai(e) : 0;
}

int SDL_WaitEventTimeout(void *e, int t) {
    static int (*vrai)(void *, int);
    if (!vrai) vrai = (int (*)(void *, int))dlsym(RTLD_NEXT, "SDL_WaitEventTimeout");
    if (a_relacher) { a_relacher = 0; relacher(); }
    return vrai ? vrai(e, t) : 0;
}

int SDL_WaitEvent(void *e) {
    static int (*vrai)(void *);
    if (!vrai) vrai = (int (*)(void *))dlsym(RTLD_NEXT, "SDL_WaitEvent");
    if (a_relacher) { a_relacher = 0; relacher(); }
    return vrai ? vrai(e) : 1;
}
