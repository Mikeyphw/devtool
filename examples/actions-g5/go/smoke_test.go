package smoke

import "testing"
func TestGoSmoke(t *testing.T) {
    if 2+3 != 5 { t.Fatal("Go test failed") }
}
