## I use this when I need some embedded language
# Original (c) Peter Norvig, 2010; See http://norvig.com/lispy2.html

from __future__ import division, unicode_literals, print_function
import itertools as it, operator as op, functools as ft


global_env = macro_table = symbol_table = None


################ Symbol, Procedure, classes

import re, sys, types, StringIO

class Symbol(unicode): pass

def Sym(s):
	'Find or create unique Symbol entry for str s in symbol table.'
	if s not in symbol_table: symbol_table[s] = Symbol(s)
	return symbol_table[s]

class Procedure(object):
	'A user-defined Scheme procedure.'
	def __init__(self, parms, exp, env):
		self.parms, self.exp, self.env = parms, exp, env
	def __call__(self, *args):
		return eval(self.exp, Env(self.parms, args, self.env))

isa = isinstance
str = types.StringTypes


################ parse, read, and user interaction

def parse(inport):
	'Parse a program: read and expand/error-check it.'
	# Backwards compatibility: given a str, convert it to an InPort
	if isa(inport, str): inport = InPort(StringIO.StringIO(inport))
	return expand(read(inport), toplevel=True)

eof_object = Symbol('#<eof-object>') # Note: uninterned; can't be read

class InPort(object):
	'An input port. Retains a line of chars.'
	tokenizer = r"""\s*(,@|[('`,)]|"(?:[\\].|[^\\"])*"|;.*|[^\s('"`,;)]*)(.*)"""
	def __init__(self, file):
		self.file = file; self.line = ''
	def next_token(self):
		'Return the next token, reading new text into line buffer if needed.'
		while True:
			if self.line == '': self.line = self.file.readline()
			if self.line == '': return eof_object
			token, self.line = re.match(InPort.tokenizer, self.line).groups()
			if token != '' and not token.startswith(';'): return token

def readchar(inport):
	'Read the next character from an input port.'
	if inport.line != '':
		ch, inport.line = inport.line[0], inport.line[1:]
		return ch
	else:
		return inport.file.read(1) or eof_object

def read(inport):
	'Read a Scheme expression from an input port.'
	def read_ahead(token):
		if '(' == token:
			L = []
			while True:
				token = inport.next_token()
				if token == ')': return L
				else: L.append(read_ahead(token))
		elif ')' == token: raise SyntaxError('unexpected )')
		elif token in quotes: return [quotes[token], read(inport)]
		elif token is eof_object: raise SyntaxError('unexpected EOF in list')
		else: return atom(token)
	# body of read:
	token1 = inport.next_token()
	return eof_object if token1 is eof_object else read_ahead(token1)

def atom(token):
	'Numbers become numbers; #t and #f are booleans; "..." string; otherwise Symbol.'
	if token == '#t': return True
	elif token == '#f': return False
	elif token[0] == '"': return token[1:-1].decode('string_escape')
	try: return int(token)
	except ValueError:
		try: return float(token)
		except ValueError:
			try: return complex(token.replace('i', 'j', 1))
			except ValueError:
				return Sym(token)

def to_string(x):
	'Convert a Python object back into a Lisp-readable string.'
	if x is True: return "#t"
	elif x is False: return "#f"
	elif isa(x, Symbol): return x
	elif isa(x, str): return '"{}"'.format(x.encode('string_escape').replace('"',r'\"'))
	elif isa(x, list): return '('+' '.join(it.imap(to_string, x))+')'
	elif isa(x, complex): return unicode(x).replace('j', 'i')
	else: return unicode(x)

def load(filename):
	'Eval every expression from a file.'
	return repl(InPort(open(filename)), out=None)

def repl(inport=InPort(sys.stdin), out=sys.stdout):
	'A prompt-read-eval-print loop.'
	val = None
	while True:
		try:
			x = parse(inport)
			if x is eof_object: return val
			val = eval(x)
			if out and val is not None: print(to_string(val), file=out)
		except Exception as e:
			if out: print('{}: {}'.format(type(e).__name__, e), file=out)
			else: raise

def peval(x): return eval(parse(x))


################ Environment class

class Env(dict):
	'An environment: a dict of {var:val} pairs, with an outer Env.'
	def __init__(self, parms=(), args=(), outer=None):
		# Bind parm list to corresponding args, or single parm to list of args
		self.outer = outer
		if isa(parms, Symbol):
			self.update({parms:list(args)})
		else:
			if len(args) != len(parms):
				raise TypeError( 'expected {}, given {}, '\
					.format(to_string(parms), to_string(args)) )
			self.update(zip(parms,args))
	def find(self, var):
		'Find the innermost Env where var appears.'
		if var in self: return self
		elif self.outer is None: raise LookupError(var)
		else: return self.outer.find(var)

def is_pair(x): return x != [] and isa(x, list)
def cons(x, y): return [x]+y

def callcc(proc):
	'Call proc with current continuation; escape only'
	ball = RuntimeWarning('Sorry, cant continue this continuation any longer.')
	def throw(retval): ball.retval = retval; raise ball
	try:
		return proc(throw)
	except RuntimeWarning as w:
		if w is ball: return ball.retval
		else: raise w

def add_globals(self):
	'Add some Scheme standard procedures.'
	import math, cmath
	self.update(vars(math))
	self.update(vars(cmath))
	self.update({
		'+':op.add, '-':op.sub, '*':op.mul, '/':op.div, 'not':op.not_,
		'>':op.gt, '<':op.lt, '>=':op.ge, '<=':op.le, '=':op.eq,
		'equal?':op.eq, 'eq?':op.is_, 'length':len, 'cons':cons,
		'car':lambda x:x[0], 'cdr':lambda x:x[1:], 'append':op.add,
		'list':lambda *x:list(x), 'list?': lambda x:isa(x,list),
		'null?':lambda x:x==[], 'symbol?':lambda x: isa(x, Symbol),
		'boolean?':lambda x: isa(x, bool), 'pair?':is_pair,
		'apply':lambda proc,l: proc(*l), 'eval':lambda x: eval(expand(x)),
		'call/cc':callcc })
	return self


################ eval (tail recursive)

def eval(x, env=None):
	'Evaluate an expression in an environment.'
	if env is None: env = global_env
	while True:
		if isa(x, Symbol): # variable reference
			return env.find(x)[x]
		elif not isa(x, list): # constant literal
			return x
		elif x[0] is _quote: # (quote exp)
			(_, exp) = x
			return exp
		elif x[0] is _if: # (if test conseq alt)
			(_, test, conseq, alt) = x
			x = (conseq if eval(test, env) else alt)
		elif x[0] is _set: # (set var exp)
			(_, var, exp) = x
			env.find(var)[var] = eval(exp, env)
			return None
		elif x[0] is _define: # (define var exp)
			(_, var, exp) = x
			env[var] = eval(exp, env)
			return env[var]
		elif x[0] is _lambda: # (lambda (var*) exp)
			(_, vars, exp) = x
			return Procedure(vars, exp, env)
		elif x[0] is _begin: # (begin exp+)
			for exp in x[1:-1]:
				eval(exp, env)
			x = x[-1]
		else: # (proc exp*)
			exps = [eval(exp, env) for exp in x]
			proc = exps.pop(0)
			if isa(proc, Procedure):
				x = proc.exp
				env = Env(proc.parms, exps, proc.env)
			else:
				try: return proc(*exps)
				except:
					print('Call failed: {} ({})'.format( proc,
						', '.join(it.imap(repr, exps)), file=sys.stderr ))
					raise


################ expand

def expand(x, toplevel=False):
	'Walk tree of x, making optimizations/fixes, and signaling SyntaxError.'
	require(x, x!=[]) # () => Error
	if not isa(x, list): # constant => unchanged
		return x
	elif x[0] is _quote: # (quote exp)
		require(x, len(x)==2)
		return x
	elif x[0] is _if:
		if len(x)==3: x = x + [None] # (if t c) => (if t c None)
		require(x, len(x)==4)
		return map(expand, x)
	elif x[0] is _set:
		require(x, len(x)==3);
		var = x[1] # (set non-var exp) => Error
		require(x, isa(var, Symbol), 'can set only a symbol')
		return [_set, var, expand(x[2])]
	elif x[0] is _define or x[0] is _definemacro:
		require(x, len(x)>=3)
		_def, v, body = x[0], x[1], x[2:]
		if isa(v, list) and v: # (define (f args) body)
			f, args = v[0], v[1:] # => (define f (lambda (args) body))
			return expand([_def, f, [_lambda, args]+body])
		else:
			require(x, len(x)==3) # (define non-var/list exp) => Error
			require(x, isa(v, Symbol), 'can define only a symbol')
			exp = expand(x[2])
			if _def is _definemacro:
				require(x, toplevel, 'define-macro only allowed at top level')
				proc = eval(exp)
				require(x, callable(proc), 'macro must be a procedure')
				macro_table[v] = proc # (define-macro v proc)
				return None # => None; add v:proc to macro_table
			return [_define, v, exp]
	elif x[0] is _begin:
		if len(x)==1: return None # (begin) => None
		else: return [expand(xi, toplevel) for xi in x]
	elif x[0] is _lambda: # (lambda (x) e1 e2)
		require(x, len(x)>=3) # => (lambda (x) (begin e1 e2))
		vars, body = x[1], x[2:]
		require(x, (isa(vars, list) and all(isa(v, Symbol) for v in vars))
				or isa(vars, Symbol), 'illegal lambda argument list')
		exp = body[0] if len(body) == 1 else [_begin] + body
		return [_lambda, vars, expand(exp)]
	elif x[0] is _quasiquote: # `x => expand_quasiquote(x)
		require(x, len(x)==2)
		return expand_quasiquote(x[1])
	elif isa(x[0], Symbol) and x[0] in macro_table:
		return expand(macro_table[x[0]](*x[1:]), toplevel) # (m arg...)
	else: # => macroexpand if m isa macro
		return map(expand, x) # (f arg...) => expand each

def require(x, predicate, msg='wrong length'):
	'Signal a syntax error if predicate is false.'
	if not predicate: raise SyntaxError(to_string(x)+': '+msg)

def expand_quasiquote(x):
	'''Expand `x => 'x; `,x => x; `(,@x y) => (append x y)'''
	if not is_pair(x):
		return [_quote, x]
	require(x, x[0] is not _unquotesplicing, 'cant splice here')
	if x[0] is _unquote:
		require(x, len(x)==2)
		return x[1]
	elif is_pair(x[0]) and x[0][0] is _unquotesplicing:
		require(x[0], len(x[0])==2)
		return [_append, x[0][1], expand_quasiquote(x[1:])]
	else:
		return [_cons, expand_quasiquote(x[0]), expand_quasiquote(x[1:])]\
			if x[0] is not _quasiquote else expand_quasiquote(expand_quasiquote(x[1:])[1])

def let(*args):
	args = list(args)
	x = cons(_let, args)
	require(x, len(args)>1)
	bindings, body = args[0], args[1:]
	require(x, all( isa(b, list) and len(b)==2
		and isa(b[0], Symbol) for b in bindings ), 'illegal binding list')
	vars, vals = zip(*bindings)
	return [[_lambda, list(vars)]+map(expand, body)] + map(expand, vals)


## Interpreter setup

def init_env(env_ext=dict()):
	global global_env, macro_table, symbol_table
	symbol_table = dict()

	global_env = add_globals(Env())
	global _quote, _if, _set, _define, _lambda, _begin, _definemacro
	_quote, _if, _set, _define, _lambda, _begin, _definemacro = it.imap(
		Sym, ['quote', 'if', 'set', 'define', 'lambda', 'begin', 'define-macro'] )
	global _quasiquote, _unquote, _unquotesplicing
	_quasiquote, _unquote, _unquotesplicing = it.imap(
		Sym, ['quasiquote', 'unquote', 'unquote-splicing'] )
	global _append, _cons, _let, quotes
	_append, _cons, _let = it.imap(Sym, ['append', 'cons', 'let'])
	quotes = {"'":_quote, '`':_quasiquote, ',':_unquote, ',@':_unquotesplicing}

	macro_table = {_let:let}

	peval('''(begin
	(define-macro and (lambda args
		(if (null? args) #t
			(if (= (length args) 1) (car args)
				`(if ,(car args) (and ,@(cdr args)) #f)))))
	(define-macro or (lambda args
		(if (null? args) #f
			(if (= (length args) 1) (car args)
				`(if ,(car args) ,(car args) (or ,@(cdr args)))))))
	)''')

	for sym,val in env_ext.iteritems(): global_env[Sym(sym)] = val
