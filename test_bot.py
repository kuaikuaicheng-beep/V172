"""CT小舖自動測試。執行：python3 test_bot.py（不需要 Discord、不需要網路）。"""
import os, sys, tempfile, unittest, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ── 讓測試在沒有安裝 discord.py 的環境也能 import bot.py：用萬用假模組取代 discord ──

class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith('__'): raise AttributeError(name)
        return Stub
    def __getitem__(cls, item): return Stub
    def __or__(cls, other): return Stub
    def __ror__(cls, other): return Stub

class Stub(metaclass=_Meta):
    def __init_subclass__(cls, **kw): pass
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k):
        # 當成 decorator 用時回傳原函式，不影響 bot.py 內的一般函式
        if len(a) == 1 and callable(a[0]) and not k: return a[0]
        return Stub()
    def __getattr__(self, name):
        if name.startswith('__'): raise AttributeError(name)
        return Stub()

def install():
    for name in ('discord', 'discord.ext', 'discord.ext.commands', 'discord.ext.tasks', 'discord.ui', 'discord.app_commands'):
        m = types.ModuleType(name)
        m.__getattr__ = lambda n, _s=Stub: _s
        sys.modules[name] = m
    sys.modules['discord'].ui = sys.modules['discord.ui']
    sys.modules['discord'].app_commands = sys.modules['discord.app_commands']
    sys.modules['discord.ext'].commands = sys.modules['discord.ext.commands']
    sys.modules['discord.ext'].tasks = sys.modules['discord.ext.tasks']
    # tasks.loop 要保留 .before_loop / .start 等屬性，所以回傳假物件而不是原函式
    sys.modules['discord.ext.tasks'].loop = lambda *a, **k: (lambda f: Stub())

install()
os.environ['DB_PATH'] = os.path.join(tempfile.mkdtemp(), 'test.db')
import bot

def reset():
    for t in ('orders','balances','balance_logs','products','accounts'): bot.q(f'DELETE FROM {t}')

def make_order(status='待付款', total=1000, service='幣號', buyer='111', reserved=0, qty=2, product='100M（普通）'):
    c = bot.db.cursor()
    c.execute('INSERT INTO orders(ticket_no,channel_id,guild_id,product,quantity,unit_price,total_price,status,buyer_id,created_at,service_type,stock_reserved) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
              ('0001','c1','g1',product,qty,total//qty,total,status,buyer,bot.now(),service,reserved))
    bot.db.commit(); return c.lastrowid

def set_bal(uid, n): bot.q('INSERT INTO balances(user_id,balance) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET balance=excluded.balance', (uid, n))
def bal(uid): return bot._balance_of(uid)

class BalancePay(unittest.TestCase):
    def setUp(self): reset()
    def test_pay_ok(self):
        set_bal('111', 1500); oid = make_order(total=1000)
        ok, msg, o = bot.pay_order_with_balance(oid, '111')
        self.assertTrue(ok, msg); self.assertEqual(bal('111'), 500)
        self.assertEqual(o['status'], '待交貨'); self.assertEqual(o['balance_paid'], 1000); self.assertEqual(o['payment_method'], '餘額')
        lg = bot.q('SELECT * FROM balance_logs',(),True); self.assertEqual(len(lg), 1); self.assertEqual(lg[0]['action'], '訂單付款'); self.assertEqual(lg[0]['balance_after'], 500)
    def test_boost_goes_to_queue(self):
        set_bal('111', 1000); oid = make_order(total=1000, service='代肝')
        ok, _, o = bot.pay_order_with_balance(oid, '111'); self.assertTrue(ok); self.assertEqual(o['status'], '待排單')
    def test_insufficient_no_deduction(self):
        set_bal('111', 999); oid = make_order(total=1000)
        ok, msg, _ = bot.pay_order_with_balance(oid, '111')
        self.assertFalse(ok); self.assertIn('還差 NT$1', msg); self.assertEqual(bal('111'), 999)
        self.assertEqual(bot.q('SELECT status FROM orders WHERE id=?',(oid,),True)[0]['status'], '待付款')
    def test_double_click_charges_once(self):
        set_bal('111', 5000); oid = make_order(total=1000)
        r1 = bot.pay_order_with_balance(oid, '111'); r2 = bot.pay_order_with_balance(oid, '111')
        self.assertTrue(r1[0]); self.assertFalse(r2[0]); self.assertEqual(bal('111'), 4000)
    def test_other_person_cannot_pay(self):
        set_bal('222', 5000); oid = make_order(total=1000, buyer='111')
        ok, _, _ = bot.pay_order_with_balance(oid, '222'); self.assertFalse(ok); self.assertEqual(bal('222'), 5000)
    def test_not_pending_cannot_pay(self):
        set_bal('111', 5000); oid = make_order(status='待交貨')
        self.assertFalse(bot.pay_order_with_balance(oid, '111')[0]); self.assertEqual(bal('111'), 5000)

class BalanceRefund(unittest.TestCase):
    def setUp(self): reset()
    def paid(self):
        set_bal('111', 1000); oid = make_order(total=1000); bot.pay_order_with_balance(oid, '111'); return oid
    def test_cancel_refunds_once(self):
        oid = self.paid(); self.assertEqual(bal('111'), 0)
        self.assertEqual(bot.refund_balance_on_cancel(oid, '待交貨', '已取消', 9), 1000); self.assertEqual(bal('111'), 1000)
        self.assertEqual(bot.refund_balance_on_cancel(oid, '待交貨', '已取消', 9), 0); self.assertEqual(bal('111'), 1000)
        self.assertEqual(bot.q("SELECT COUNT(*) n FROM balance_logs WHERE action='訂單退款'",(),True)[0]['n'], 1)
    def test_delivered_not_auto_refunded(self):
        oid = self.paid()
        self.assertEqual(bot.refund_balance_on_cancel(oid, '待收貨', '已取消', 9), 0); self.assertEqual(bal('111'), 0)
    def test_non_cancel_no_refund(self):
        oid = self.paid(); self.assertEqual(bot.refund_balance_on_cancel(oid, '待交貨', '處理中', 9), 0)
    def test_transfer_order_no_refund(self):
        oid = make_order(status='待交貨'); self.assertEqual(bot.refund_balance_on_cancel(oid, '待交貨', '已取消', 9), 0)

class StockRelease(unittest.TestCase):
    def setUp(self): reset(); bot.q("INSERT INTO products(product,stock) VALUES('100M（普通）',5) ON CONFLICT(product) DO UPDATE SET stock=5")
    def test_cancel_returns_stock_once(self):
        oid = make_order(reserved=1, qty=2)
        self.assertTrue(bot.release_reserved_stock(oid, '待付款', '已取消')); self.assertEqual(bot.stock('100M（普通）'), 7)
        self.assertFalse(bot.release_reserved_stock(oid, '待付款', '已取消')); self.assertEqual(bot.stock('100M（普通）'), 7)
    def test_delivered_keeps_stock(self):
        oid = make_order(reserved=1, qty=2); self.assertFalse(bot.release_reserved_stock(oid, '待收貨', '已取消')); self.assertEqual(bot.stock('100M（普通）'), 5)

class Helpers(unittest.TestCase):
    def test_safe_format(self):
        self.assertEqual(bot.safe_format('銀行 {bank} {x} {} JSON{"a":1}', bank='中信'), '銀行 中信 {x} {} JSON{"a":1}')
    def test_db_snapshot_is_valid_zip_with_data(self):
        import zipfile, io, sqlite3
        reset(); set_bal('111', 321)
        z = zipfile.ZipFile(io.BytesIO(bot.db_snapshot_zip())); p = os.path.join(tempfile.mkdtemp(), 'x.db'); open(p, 'wb').write(z.read('trade_bot.db'))
        self.assertEqual(sqlite3.connect(p).execute("SELECT balance FROM balances WHERE user_id='111'").fetchone()[0], 321)
    def test_low_stock_threshold_default_and_off(self):
        class G: id = 1
        self.assertEqual(bot.low_stock_threshold(1), 3); bot.setv(1, 'low_stock_threshold', 0); self.assertEqual(bot.low_stock_threshold(1), 0)

class ShopPause(unittest.TestCase):
    def test_pause_flag(self):
        bot.setv(7, 'shop_status', '營業中'); self.assertFalse(bot.shop_paused(7))
        bot.setv(7, 'shop_status', '休息中'); self.assertFalse(bot.shop_paused(7))   # 休息中只是標示，不擋下單
        bot.setv(7, 'shop_status', '暫停接單'); self.assertTrue(bot.shop_paused(7))
    def test_paused_message_includes_notice_and_custom_text(self):
        bot.setv(8, 'rest_text', '今晚 10 點回來'); self.assertIn('今晚 10 點回來', bot.paused_message(8))
        bot.setv(8, 'paused_text', '休息一下'); self.assertTrue(bot.paused_message(8).startswith('休息一下'))
    def test_restock_line(self):
        self.assertEqual(bot.restock_line(9, 'X'), ''); bot.setv(9, 'restock_X', '週五晚上'); self.assertIn('週五晚上', bot.restock_line(9, 'X'))

class FakeMember(bot.discord.Member):
    def __init__(self, uid, roles=(), admin=False, is_bot=False, gid=50):
        self.id = uid; self.bot = is_bot; self.display_name = f'u{uid}'
        self.guild = types.SimpleNamespace(id=gid)
        self.guild_permissions = types.SimpleNamespace(administrator=admin, manage_guild=False)
        self.roles = [types.SimpleNamespace(id=1000 + n, name=r) for n, r in enumerate(roles)]

class BuyerDetection(unittest.TestCase):
    def test_shop_owner_is_never_the_customer(self):
        bot.setv(50, 'ticket_owner_id', '7')
        self.assertTrue(bot._is_staff_member(FakeMember(7)))                 # 設定的店長
        self.assertFalse(bot._is_staff_member(FakeMember(8)))                # 一般客人
    def test_boss_role_names_are_staff(self):
        for name in ('店長', '店長👑', '闆闆👑', '管理員'):
            self.assertTrue(bot._is_staff_member(FakeMember(9, roles=(name,))), name)
    def test_admin_permission_and_bots_are_staff(self):
        self.assertTrue(bot._is_staff_member(FakeMember(10, admin=True))); self.assertTrue(bot._is_staff_member(FakeMember(11, is_bot=True)))

class ForeignTicketGuard(unittest.TestCase):
    def setUp(self):
        reset(); bot.q('DELETE FROM ticket_channels'); bot.setv(50, 'ticket_owner_id', '')
        bot.q("INSERT INTO ticket_channels(channel_id,guild_id,ticket_no,first_name,buyer_id,buyer_name,detected_at) VALUES('c9','50','0009','x','100','客人A',?)", (bot.now(),))
    def inter(self, user, members=()):
        users = {m.id: m for m in members}
        return types.SimpleNamespace(user=user, guild=types.SimpleNamespace(id=50, get_member=lambda uid: users.get(uid)))
    ch = types.SimpleNamespace(id='c9')
    def test_other_person_is_blocked(self):
        self.assertIn('<@100>', bot.foreign_ticket_notice(self.inter(FakeMember(200)), self.ch))
    def test_real_buyer_ok(self):
        self.assertIsNone(bot.foreign_ticket_notice(self.inter(FakeMember(100)), self.ch))
    def test_non_ticket_channel_ok(self):
        self.assertIsNone(bot.foreign_ticket_notice(self.inter(FakeMember(200)), types.SimpleNamespace(id='nope')))
    def test_self_heal_when_recorded_buyer_was_staff(self):
        boss = FakeMember(100, roles=('店長',)); customer = FakeMember(200)
        self.assertIsNone(bot.foreign_ticket_notice(self.inter(customer, members=[boss]), self.ch))
        self.assertEqual(bot.q("SELECT buyer_id FROM ticket_channels WHERE channel_id='c9'", (), True)[0]['buyer_id'], '200')


class SplitAccounts(unittest.TestCase):
    def test_em_dash_inline(self):
        self.assertEqual(bot.split_account_blocks('a----1 ————— b----2 ————— c----3'),['a----1','b----2','c----3'])
    def test_em_dash_lines_multiline_blocks(self):
        t='帳號：a\n密碼：1\n—————\n帳號：b\n密碼：2\n—————\n'
        self.assertEqual(bot.split_account_blocks(t),['帳號：a\n密碼：1','帳號：b\n密碼：2'])
    def test_ascii_line_separator(self):
        self.assertEqual(bot.split_account_blocks('a----1\n-----\nb----2'),['a----1','b----2'])
    def test_no_separator_one_per_line(self):
        self.assertEqual(bot.split_account_blocks('a----1\n\nb----2\n'),['a----1','b----2'])
    def test_field_separator_not_split(self):
        self.assertEqual(bot.split_account_blocks('帳號：a----密碼：1'),['帳號：a----密碼：1'])
    def test_labels(self):
        self.assertEqual(bot._lt_label(''),'未指定登入'); self.assertIn('',bot.POOL_TYPES)


class AccountFormat(unittest.TestCase):
    def test_multiline_account_fields_split(self):
        md = bot._account_md('帳號：u2\n密碼：pw2')
        self.assertIn('**帳號**\n```\nu2\n```', md); self.assertIn('**密碼**\n```\npw2\n```', md)
    def test_inline_still_works(self):
        md = bot._account_md('帳號：a----密碼：b')
        self.assertIn('**帳號**', md); self.assertIn('**密碼**', md)


class DumpCommand(unittest.TestCase):
    def test_registered_and_sorted_helpers(self):
        self.assertTrue(callable(bot.account_stock_dump))
    def test_file_roundtrip_blocks(self):
        # 檔案內容（不含標題行）用 ————— 分開，能被 split_account_blocks 還原成原本的帳號
        body = '\n—————\n'.join(['u1', '帳號：u2\n密碼：pw2'])
        self.assertEqual(bot.split_account_blocks(body), ['u1', '帳號：u2\n密碼：pw2'])


class Giveaway(unittest.TestCase):
    def setUp(self):
        bot.q('DELETE FROM giveaways'); bot.q('DELETE FROM giveaway_entries')
    def _g(self, winners=1):
        c = bot.db.cursor()
        c.execute('INSERT INTO giveaways(guild_id,channel_id,prize,winners,host_id,created_at) VALUES(?,?,?,?,?,?)', ('g1','c1','100M',winners,'h',bot.now()))
        bot.db.commit(); return c.lastrowid
    def test_pick_count_and_unique(self):
        r = bot.giveaway_pick(['1','2','3','4','5'], 3)
        self.assertEqual(len(r), 3); self.assertEqual(len(set(r)), 3)
    def test_pick_more_than_entries(self):
        self.assertEqual(sorted(bot.giveaway_pick(['1','2'], 5)), ['1','2'])
    def test_pick_empty(self):
        self.assertEqual(bot.giveaway_pick([], 3), [])
    def test_pick_excludes_old_winners(self):
        for _ in range(30):
            self.assertEqual(bot.giveaway_pick(['1','2','3'], 1, exclude=['1','2']), ['3'])
    def test_pick_dedupes_entries(self):
        self.assertEqual(bot.giveaway_pick(['1','1','1'], 3), ['1'])
    def test_one_entry_per_user(self):
        gid = self._g(); c = bot.db.cursor()
        for _ in range(3): c.execute('INSERT OR IGNORE INTO giveaway_entries(giveaway_id,user_id,joined_at) VALUES(?,?,?)', (gid,'9',bot.now()))
        bot.db.commit(); self.assertEqual(bot.giveaway_count(gid), 1)
    def test_embed_builds(self):
        gid = self._g(2); bot.giveaway_embed(bot.giveaway_get(gid)); bot.giveaway_embed(bot.giveaway_get(gid), ended=True, winner_ids=['1'])
        bot.giveaway_embed(bot.giveaway_get(gid), ended=True, cancelled=True)


if __name__ == '__main__': unittest.main(verbosity=2)
