#include <Wire.h>

const uint8_t BINT_PIN = 2;
const uint8_t EEPROM = 0x50;
const uint16_t EEPROM_SIZE = 512;
const uint8_t MAX_ARGS = 34;

char line[112];
uint8_t len;
bool overflow;
bool held;
bool gate = true;

void setBint(bool on) {
  held = on;
  digitalWrite(BINT_PIN, LOW);
  pinMode(BINT_PIN, on ? OUTPUT : INPUT);
}

void err(const __FlashStringHelper *why) {
  Serial.print(F("ERR "));
  Serial.println(why);
}

void hex(uint16_t v, uint8_t width) {
  for (int8_t s = (width - 1) * 4; s >= 0; s -= 4) Serial.print((v >> s) & 0xF, HEX);
}

bool parse(const char *s, uint16_t max, uint16_t &v) {
  char *end;
  unsigned long x = strtoul(s, &end, 16);
  if (!*s || *end || x > max) return false;
  v = x;
  return true;
}

uint8_t dev(uint16_t off) {
  return EEPROM + (off >> 8);
}

bool failed(uint8_t code, uint8_t addr) {
  if (!code) return false;
  Serial.print(F("ERR i2c "));
  hex(addr, 2);
  Serial.print(' ');
  Serial.println(code);
  return true;
}

uint8_t regRead(uint8_t addr, uint8_t reg, uint8_t *buf, uint8_t n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  uint8_t e = Wire.endTransmission(false);
  if (e) return e;
  if (Wire.requestFrom(addr, n) != n) return 4;
  for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
  return 0;
}

uint8_t eeRead(uint16_t off, uint8_t *buf, uint8_t n) {
  return regRead(dev(off), off & 0xFF, buf, n);
}

uint8_t eeWrite(uint16_t off, uint8_t b) {
  Wire.beginTransmission(dev(off));
  Wire.write(off & 0xFF);
  Wire.write(b);
  uint8_t e = Wire.endTransmission();
  delay(10);
  return e;
}

void printBytes(const uint8_t *buf, uint8_t n) {
  for (uint8_t i = 0; i < n; i++) {
    Serial.print(' ');
    hex(buf[i], 2);
  }
}

void cmdBint(char **a, uint8_t n) {
  if (n == 2 && !strcmp(a[1], "1")) setBint(true);
  else if (n == 2 && !strcmp(a[1], "0")) setBint(false);
  else if (n != 1) return err(F("args"));
  Serial.println(held ? F("OK 1") : F("OK 0"));
}

void cmdGate(char **a, uint8_t n) {
  if (n == 2 && !strcmp(a[1], "1")) gate = true;
  else if (n == 2 && !strcmp(a[1], "0")) gate = false;
  else if (n != 1) return err(F("args"));
  Serial.println(gate ? F("OK 1") : F("OK 0"));
}

void cmdScan(char **a, uint8_t n) {
  uint16_t lo = 0x08, hi = 0x77;
  if (n == 2 || n > 3 || (n == 3 && (!parse(a[1], 0x7F, lo) || !parse(a[2], 0x7F, hi) || lo > hi)))
    return err(F("args"));
  Serial.print(F("OK"));
  for (uint8_t addr = lo; addr <= hi; addr++) {
    Wire.beginTransmission(addr);
    if (!Wire.endTransmission()) {
      Serial.print(' ');
      hex(addr, 2);
    }
  }
  Serial.println();
}

void cmdRead(char **a, uint8_t n) {
  uint16_t off, cnt = 1;
  if (n < 2 || n > 3 || !parse(a[1], EEPROM_SIZE - 1, off)
      || (n == 3 && !parse(a[2], EEPROM_SIZE, cnt)) || !cnt || off + cnt > EEPROM_SIZE)
    return err(F("args"));
  uint8_t buf[16];
  if (cnt <= 16) {
    for (uint8_t i = 0, k; i < cnt; i += k) {
      k = min(cnt - i, 0x100 - ((off + i) & 0xFF));
      if (failed(eeRead(off + i, buf + i, k), dev(off + i))) return;
    }
    Serial.print(F("OK"));
    printBytes(buf, cnt);
    Serial.println();
    return;
  }
  uint16_t sum = 0;
  while (cnt) {
    uint8_t k = min(cnt, 16 - (off & 15));
    if (failed(eeRead(off, buf, k), dev(off))) return;
    hex(off, 3);
    Serial.print(':');
    printBytes(buf, k);
    Serial.println();
    for (uint8_t i = 0; i < k; i++) sum += buf[i];
    off += k;
    cnt -= k;
  }
  Serial.print(F("OK "));
  hex(sum, 4);
  Serial.println();
}

void cmdWrite(char **a, uint8_t n) {
  uint16_t off, v;
  uint8_t data[MAX_ARGS];
  if (n < 3 || !parse(a[1], EEPROM_SIZE - 1, off) || off + n - 2 > EEPROM_SIZE) return err(F("args"));
  uint8_t cnt = n - 2;
  for (uint8_t i = 0; i < cnt; i++) {
    if (!parse(a[i + 2], 0xFF, v)) return err(F("args"));
    data[i] = v;
  }
  for (uint8_t i = 0; i < cnt; i++)
    if (failed(eeWrite(off + i, data[i]), dev(off + i))) return;
  for (uint8_t i = 0; i < cnt; i++) {
    uint8_t got;
    if (failed(eeRead(off + i, &got, 1), dev(off + i))) return;
    if (got != data[i]) {
      Serial.print(F("ERR verify "));
      hex(off + i, 3);
      Serial.print(' ');
      hex(got, 2);
      Serial.println();
      return;
    }
  }
  Serial.println(F("OK"));
}

void cmdI2cRead(char **a, uint8_t n) {
  uint16_t addr, reg, cnt = 1;
  uint8_t buf[16];
  if (n < 3 || n > 4 || !parse(a[1], 0x7F, addr) || !parse(a[2], 0xFF, reg)
      || (n == 4 && !parse(a[3], 16, cnt)) || !cnt)
    return err(F("args"));
  if (failed(regRead(addr, reg, buf, cnt), addr)) return;
  Serial.print(F("OK"));
  printBytes(buf, cnt);
  Serial.println();
}

void cmdI2cWrite(char **a, uint8_t n) {
  uint16_t addr, v;
  uint8_t data[MAX_ARGS];
  if (n < 3 || !parse(a[1], 0x7F, addr)) return err(F("args"));
  for (uint8_t i = 2; i < n; i++) {
    if (!parse(a[i], 0xFF, v)) return err(F("args"));
    data[i - 2] = v;
  }
  Wire.beginTransmission((uint8_t)addr);
  Wire.write(data, n - 2);
  if (failed(Wire.endTransmission(), addr)) return;
  Serial.println(F("OK"));
}

void run() {
  char *a[MAX_ARGS];
  uint8_t n = 0;
  for (char *t = strtok(line, " \t"); t; t = strtok(NULL, " \t")) {
    if (n == MAX_ARGS) return err(F("args"));
    a[n++] = t;
  }
  if (!n) return;
  char c = a[0][1] ? 0 : a[0][0];
  if (c == 'b') return cmdBint(a, n);
  if (c == 'g') return cmdGate(a, n);
  if (c == 's') return cmdScan(a, n);
  if (!c || !strchr("rwio", c)) return err(F("cmd"));
  if (gate && !held) return err(F("bint"));
  if (c == 'r') cmdRead(a, n);
  else if (c == 'w') cmdWrite(a, n);
  else if (c == 'i') cmdI2cRead(a, n);
  else cmdI2cWrite(a, n);
}

void setup() {
  setBint(false);
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(100000);
  Wire.setWireTimeout(25000, true);
  Serial.println(F("READY pvm 1"));
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r' || c == '\n') {
      line[len] = 0;
      if (overflow) err(F("args"));
      else run();
      len = 0;
      overflow = false;
    } else if (c == '\b' || c == 0x7F) {
      if (len) len--;
    } else if (len < sizeof(line) - 1) {
      line[len++] = tolower(c);
    } else {
      overflow = true;
    }
  }
}
