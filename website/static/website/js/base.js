const btn = document.getElementById('js-btn');
const nav = document.getElementById('js-nav');

btn.addEventListener('click', function(){
	btn.classList.toggle('active');
	nav.classList.toggle('active');
})
