/*
 * n64train-input — minimal mupen64plus input plugin.
 *
 * Reads N64 controller button state from a memory-mapped file.
 * Path defaults to /tmp/mk4_ctrl but can be overridden per-instance:
 *   N64TRAIN_CTRL_P1=/tmp/mk4_ctrl_myrun_0  (player 1)
 *   N64TRAIN_CTRL_P2=/tmp/mk4_ctrl_p2       (player 2, rarely used)
 *
 * Layout (4 bytes):
 *   bytes 0-1  uint16_t  buttons   — N64 BUTTONS.Value lower 16 bits
 *   byte  2    int8_t    x_axis
 *   byte  3    int8_t    y_axis
 *
 * N64 BUTTONS bitmask:
 *   bit 0  R_DPAD   bit 1  L_DPAD   bit 2  D_DPAD   bit 3  U_DPAD
 *   bit 4  START    bit 5  Z_TRIG   bit 6  B_BUTTON bit 7  A_BUTTON
 *   bit 8  R_CBUTN  bit 9  L_CBUTN  bit 10 D_CBUTN  bit 11 U_CBUTN
 *   bit 12 R_TRIG   bit 13 L_TRIG
 */

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

/* ── mupen64plus plugin API ─────────────────────────────────────────────────
 */
#define M64P_PLUGIN_API_VERSION 0x020100
#define M64P_CORE_API_VERSION 0x020001
#define PLUGIN_INPUT 4
#define PLUGIN_NONE 1
#define CONT_TYPE_STANDARD 0 /* from m64p_plugin.h */
#define M64ERR_SUCCESS 0
#define M64ERR_INPUT_NOT_FOUND 6

typedef int32_t m64p_error;

/* Match official m64p_plugin.h exactly: fields are int, not uint8_t */
typedef struct {
  int Present;
  int RawData;
  int Plugin;
  int Type;
} CONTROL;

typedef struct {
  CONTROL *Controls;
} CONTROL_INFO;

typedef union {
  uint32_t Value;
  struct {
    unsigned R_DPAD : 1;
    unsigned L_DPAD : 1;
    unsigned D_DPAD : 1;
    unsigned U_DPAD : 1;
    unsigned START : 1;
    unsigned Z_TRIG : 1;
    unsigned B_BUTTON : 1;
    unsigned A_BUTTON : 1;
    unsigned R_CBUTTON : 1;
    unsigned L_CBUTTON : 1;
    unsigned D_CBUTTON : 1;
    unsigned U_CBUTTON : 1;
    unsigned R_TRIG : 1;
    unsigned L_TRIG : 1;
    unsigned : 2;
    signed Y_AXIS : 8;
    signed X_AXIS : 8;
  };
} BUTTONS;

#define CTRL_FILE_P1_DEFAULT "/tmp/mk4_ctrl"
#define CTRL_FILE_P2_DEFAULT "/tmp/mk4_ctrl_p2"
#define CTRL_SIZE 4

static const char *ctrl_path_p1(void) {
  const char *env = getenv("N64TRAIN_CTRL_P1");
  return (env && env[0]) ? env : CTRL_FILE_P1_DEFAULT;
}

static const char *ctrl_path_p2(void) {
  const char *env = getenv("N64TRAIN_CTRL_P2");
  /* Return NULL when unset — caller must skip P2 ctrl_open().
   * This lets MK4's built-in CPU AI control P2 natively. */
  return (env && env[0]) ? env : NULL;
}

typedef struct {
  uint16_t buttons;
  int8_t x_axis;
  int8_t y_axis;
} CtrlState;

static volatile CtrlState *g_ctrl[2] = {NULL, NULL};
static int g_ctrl_fd[2] = {-1, -1};

static void ctrl_open(int idx, const char *path) {
  g_ctrl_fd[idx] = open(path, O_CREAT | O_RDWR, 0666);
  if (g_ctrl_fd[idx] < 0) {
    perror("n64train-input: open");
    return;
  }
  if (ftruncate(g_ctrl_fd[idx], CTRL_SIZE) < 0) {
    perror("n64train-input: ftruncate");
    return;
  }
  g_ctrl[idx] = (volatile CtrlState *)mmap(
      NULL, CTRL_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, g_ctrl_fd[idx], 0);
  if (g_ctrl[idx] == MAP_FAILED) {
    perror("n64train-input: mmap");
    g_ctrl[idx] = NULL;
    return;
  }
  memset((void *)g_ctrl[idx], 0, CTRL_SIZE);
  fprintf(stderr, "[n64train-input] P%d ready: %s\n", idx + 1, path);
}

static void ctrl_close_all(void) {
  for (int i = 0; i < 2; i++) {
    if (g_ctrl[i]) {
      munmap((void *)g_ctrl[i], CTRL_SIZE);
      g_ctrl[i] = NULL;
    }
    if (g_ctrl_fd[i] >= 0) {
      close(g_ctrl_fd[i]);
      g_ctrl_fd[i] = -1;
    }
  }
}

/* ── Plugin API exports ─────────────────────────────────────────────────────
 */

__attribute__((visibility("default"))) m64p_error PluginGetVersion(
    int *type, int *ver, int *api_ver, const char **name, int *caps) {
  if (type)
    *type = PLUGIN_INPUT;
  if (ver)
    *ver = 0x000001;
  if (api_ver)
    *api_ver = M64P_PLUGIN_API_VERSION;
  if (name)
    *name = "n64train-input";
  if (caps)
    *caps = 0;
  return M64ERR_SUCCESS;
}

__attribute__((visibility("default"))) m64p_error
PluginStartup(void *core_lib, void *ctx, void *debug_cb) {
  (void)core_lib;
  (void)ctx;
  (void)debug_cb;
  return M64ERR_SUCCESS;
}

__attribute__((visibility("default"))) void
InitiateControllers(CONTROL_INFO info) {
  memset(info.Controls, 0, 4 * sizeof(CONTROL));
  /* P1 is always present and intercepted */
  info.Controls[0].Present = 1;
  info.Controls[0].Plugin = PLUGIN_NONE;
  info.Controls[0].Type = CONT_TYPE_STANDARD;
  ctrl_open(0, ctrl_path_p1());
  /* Only register P2 as present when N64TRAIN_CTRL_P2 is explicitly set.
   * If P2 is marked Present but not intercepted, MK4 thinks it's a 2-player
   * game and disables the CPU AI — P2 gets zero input and just stands idle.
   * When P2 is NOT present, MK4 keeps arcade mode and the CPU AI fights. */
  const char *p2path = ctrl_path_p2();
  if (p2path) {
    info.Controls[1].Present = 1;
    info.Controls[1].Plugin = PLUGIN_NONE;
    info.Controls[1].Type = CONT_TYPE_STANDARD;
    ctrl_open(1, p2path);
  } else {
    info.Controls[1].Present = 0;
    fprintf(stderr, "[n64train-input] P2: NOT present (CPU AI will control)\n");
  }
}

__attribute__((visibility("default"))) void GetKeys(int control,
                                                    BUTTONS *keys) {
  keys->Value = 0;
  if (control < 0 || control > 1 || !g_ctrl[control])
    return;
  keys->Value = (uint32_t)g_ctrl[control]->buttons;
  keys->X_AXIS = g_ctrl[control]->x_axis;
  keys->Y_AXIS = g_ctrl[control]->y_axis;
  if (g_ctrl[control]->buttons || g_ctrl[control]->x_axis ||
      g_ctrl[control]->y_axis) {
    FILE *dbg = fopen("/tmp/n64train_input.log", "a");
    if (dbg) {
      fprintf(dbg, "P%d GetKeys: buttons=0x%04X x=%d y=%d\n", control + 1,
              (unsigned)g_ctrl[control]->buttons, (int)g_ctrl[control]->x_axis,
              (int)g_ctrl[control]->y_axis);
      fclose(dbg);
    }
  }
}

__attribute__((visibility("default"))) void
ControllerCommand(int control, unsigned char *cmd) {
  (void)control;
  (void)cmd;
}

__attribute__((visibility("default"))) void ReadController(int control,
                                                           unsigned char *cmd) {
  (void)control;
  (void)cmd;
}

__attribute__((visibility("default"))) void RomOpen(void) {}

__attribute__((visibility("default"))) void RomClosed(void) {
  ctrl_close_all();
}

__attribute__((visibility("default"))) m64p_error PluginShutdown(void) {
  ctrl_close_all();
  return M64ERR_SUCCESS;
}

__attribute__((visibility("default"))) void keyDown(int mod, int key) {
  (void)mod;
  (void)key;
}

__attribute__((visibility("default"))) void keyUp(int mod, int key) {
  (void)mod;
  (void)key;
}

__attribute__((visibility("default"))) void SDL_KeyDown(int mod, int key) {
  (void)mod;
  (void)key;
}

__attribute__((visibility("default"))) void SDL_KeyUp(int mod, int key) {
  (void)mod;
  (void)key;
}
